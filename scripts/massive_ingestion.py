"""
Incremental bulk ingestion of the complete StatsBomb open dataset.

Strategy:
  - ingested_matches table tracks which match_ids are done; skips them.
  - Each competition_id gets its own PostgreSQL partition on first encounter.
  - Events are bulk-inserted via pandas.to_sql (multi-row VALUES), not row-by-row.
  - A configurable REQUEST_DELAY prevents hammering the StatsBomb API.
  - Failed matches are recorded with status='error' and the pipeline continues.
"""

import gc
import logging
import os
import time
import uuid
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text
from statsbombpy import sb

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_URL = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:{os.environ.get('POSTGRES_PORT', '5432')}"
    f"/{os.environ['POSTGRES_DB']}"
)

CHUNK_SIZE = 1000
REQUEST_DELAY = 0.3  # seconds between sb.events() calls


def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True, pool_size=2, max_overflow=2)


# ─── Partition management ─────────────────────────────────────────────────────


def ensure_partition(engine, competition_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("SELECT create_competition_partition(:cid)"),
            {"cid": competition_id},
        )


# ─── Dimension loaders ───────────────────────────────────────────────────────


def upsert_competitions(engine, competitions: pd.DataFrame) -> None:
    records = (
        competitions[["competition_id", "competition_name", "country_name"]]
        .drop_duplicates("competition_id")
        .to_dict("records")
    )
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO dim_competitions (competition_id, competition_name, country_name)
                VALUES (:competition_id, :competition_name, :country_name)
                ON CONFLICT (competition_id) DO NOTHING
            """),
            records,
        )
    log.info("dim_competitions: %d rows upserted", len(records))


def upsert_seasons(engine, competitions: pd.DataFrame) -> None:
    records = (
        competitions[["season_id", "season_name"]]
        .drop_duplicates("season_id")
        .to_dict("records")
    )
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO dim_seasons (season_id, season_name)
                VALUES (:season_id, :season_name)
                ON CONFLICT (season_id) DO NOTHING
            """),
            records,
        )


def upsert_match(engine, row: pd.Series) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO dim_matches
                    (match_id, competition_id, season_id, match_date,
                     home_team, away_team, home_score, away_score, stage)
                VALUES
                    (:match_id, :competition_id, :season_id, :match_date,
                     :home_team, :away_team, :home_score, :away_score, :stage)
                ON CONFLICT (match_id) DO NOTHING
            """),
            {
                "match_id": int(row["match_id"]),
                "competition_id": int(row["competition_id"]),
                "season_id": int(row["season_id"]),
                "match_date": row.get("match_date"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "home_score": (
                    int(row["home_score"]) if pd.notna(row.get("home_score")) else None
                ),
                "away_score": (
                    int(row["away_score"]) if pd.notna(row.get("away_score")) else None
                ),
                "stage": row.get("competition_stage"),
            },
        )


def upsert_teams(engine, events: pd.DataFrame) -> None:
    teams = (
        events[["team_id", "team"]]
        .dropna(subset=["team_id"])
        .drop_duplicates("team_id")
        .rename(columns={"team": "team_name"})
        .assign(country=None)
    )
    teams["team_id"] = teams["team_id"].astype(int)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO dim_teams (team_id, team_name, country)
                VALUES (:team_id, :team_name, :country)
                ON CONFLICT (team_id) DO NOTHING
            """),
            teams.to_dict("records"),
        )


def upsert_players(engine, events: pd.DataFrame) -> None:
    players = (
        events[["player_id", "player", "position"]]
        .dropna(subset=["player_id"])
        .drop_duplicates("player_id")
        .rename(columns={"player": "player_name"})
        .assign(nationality=None)
    )
    players["player_id"] = players["player_id"].astype(int)
    players["position"] = players["position"].apply(
        lambda x: x.get("name") if isinstance(x, dict) else x
    )
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO dim_players (player_id, player_name, nationality, position)
                VALUES (:player_id, :player_name, :nationality, :position)
                ON CONFLICT (player_id) DO NOTHING
            """),
            players.to_dict("records"),
        )


# ─── Event flattening ────────────────────────────────────────────────────────


def _coord(val, idx: int) -> Optional[float]:
    if isinstance(val, list) and len(val) > idx:
        return float(val[idx])
    return None


def _end_location(row) -> tuple:
    for key in ("pass_end_location", "carry_end_location", "shot_end_location"):
        val = row.get(key)
        if isinstance(val, list) and len(val) >= 2:
            return float(val[0]), float(val[1])
    return None, None


def _outcome(row) -> Optional[str]:
    for col in ("pass_outcome", "shot_outcome", "duel_outcome", "clearance_outcome"):
        v = row.get(col)
        if pd.notna(v):
            return str(v)
    return None


def flatten_events(
    events: pd.DataFrame, match_id: int, competition_id: int
) -> pd.DataFrame:
    df = events.copy()

    df["event_id"] = df["id"].apply(
        lambda x: str(uuid.UUID(str(x))) if pd.notna(x) else str(uuid.uuid4())
    )
    df["match_id"] = match_id
    df["competition_id"] = competition_id
    df["event_type"] = df["type"].apply(
        lambda x: x.get("name") if isinstance(x, dict) else x
    )
    df["minute"] = df["minute"].astype("Int64")
    df["second"] = df["second"].astype("Int64")
    df["location_x"] = df["location"].apply(lambda x: _coord(x, 0))
    df["location_y"] = df["location"].apply(lambda x: _coord(x, 1))
    df["team_id"] = pd.to_numeric(df.get("team_id"), errors="coerce").astype("Int64")
    df["player_id"] = pd.to_numeric(df.get("player_id"), errors="coerce").astype(
        "Int64"
    )

    end_locs = df.apply(_end_location, axis=1)
    df["end_location_x"] = [e[0] for e in end_locs]
    df["end_location_y"] = [e[1] for e in end_locs]
    df["outcome"] = df.apply(_outcome, axis=1)
    df["under_pressure"] = (
        df.get("under_pressure", pd.Series(False, index=df.index))
        .fillna(False)
        .astype(bool)
    )
    df["xt_value"] = None
    df["xp_value"] = None
    df["xg_value"] = None
    df["raw_json"] = df.apply(lambda r: r.to_json(), axis=1)

    return df[
        [
            "event_id",
            "match_id",
            "competition_id",
            "team_id",
            "player_id",
            "event_type",
            "minute",
            "second",
            "location_x",
            "location_y",
            "end_location_x",
            "end_location_y",
            "outcome",
            "under_pressure",
            "xt_value",
            "xp_value",
            "xg_value",
            "raw_json",
        ]
    ]


# ─── Bulk insert ─────────────────────────────────────────────────────────────


def bulk_insert_events(engine, flat: pd.DataFrame) -> None:
    """Bulk insert via pandas to_sql (multi-row VALUES).

    raw_json is excluded: JSONB requires explicit casting which to_sql cannot
    express. The column defaults to NULL and can be backfilled separately if needed.
    """
    insert_df = flat.drop(columns=["raw_json"], errors="ignore").copy()

    for col in ("team_id", "player_id", "minute", "second"):
        insert_df[col] = (
            insert_df[col].astype(object).where(insert_df[col].notna(), None)
        )

    insert_df.to_sql(
        "fact_events",
        engine,
        if_exists="append",
        index=False,
        method="multi",
        chunksize=CHUNK_SIZE,
    )


# ─── Match ingestion ─────────────────────────────────────────────────────────


def get_ingested_ids(engine) -> set:
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT match_id FROM ingested_matches WHERE status = 'success'")
        )
        return {r[0] for r in result}


def mark_ingested(
    engine,
    match_id: int,
    competition_id: int,
    event_count: int,
    status: str = "success",
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO ingested_matches (match_id, competition_id, event_count, status)
                VALUES (:mid, :cid, :cnt, :status)
                ON CONFLICT (match_id) DO UPDATE SET status = EXCLUDED.status, ingested_at = NOW()
            """),
            {
                "mid": match_id,
                "cid": competition_id,
                "cnt": event_count,
                "status": status,
            },
        )


def ingest_match(engine, match_id: int, competition_id: int) -> None:
    events = sb.events(match_id=match_id)
    if events.empty:
        log.warning("match_id=%d returned empty events", match_id)
        return

    upsert_teams(engine, events)
    upsert_players(engine, events)

    flat = flatten_events(events, match_id, competition_id)
    bulk_insert_events(engine, flat)
    mark_ingested(engine, match_id, competition_id, len(flat))

    del events, flat
    gc.collect()


# ─── Entry point ─────────────────────────────────────────────────────────────


def run(competition_filter: Optional[list] = None) -> None:
    engine = get_engine()

    competitions = sb.competitions()
    upsert_competitions(engine, competitions)
    upsert_seasons(engine, competitions)

    if competition_filter:
        competitions = competitions[
            competitions["competition_id"].isin(competition_filter)
        ]

    ingested = get_ingested_ids(engine)
    log.info("Already ingested: %d matches", len(ingested))

    for _, comp_row in competitions.iterrows():
        cid = int(comp_row["competition_id"])
        sid = int(comp_row["season_id"])

        ensure_partition(engine, cid)

        try:
            matches = sb.matches(competition_id=cid, season_id=sid)
        except Exception as exc:
            log.error("Failed to fetch matches cid=%d sid=%d: %s", cid, sid, exc)
            continue

        pending = matches[~matches["match_id"].isin(ingested)]
        log.info(
            "competition=%d season=%d pending=%d/%d",
            cid,
            sid,
            len(pending),
            len(matches),
        )

        for _, match_row in pending.iterrows():
            mid = int(match_row["match_id"])
            upsert_match(engine, match_row)

            try:
                ingest_match(engine, mid, cid)
                log.info("Ingested match_id=%d", mid)
                ingested.add(mid)
            except Exception as exc:
                log.error("Failed match_id=%d: %s", mid, exc)
                mark_ingested(engine, mid, cid, 0, status="error")

            time.sleep(REQUEST_DELAY)

    log.info("Ingestion complete. Total ingested: %d", len(ingested))


if __name__ == "__main__":
    run()
