CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- ─── Dimension Tables ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dim_competitions (
    competition_id   INTEGER PRIMARY KEY,
    competition_name TEXT    NOT NULL,
    country_name     TEXT
);

CREATE TABLE IF NOT EXISTS dim_seasons (
    season_id   INTEGER PRIMARY KEY,
    season_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_matches (
    match_id       INTEGER PRIMARY KEY,
    competition_id INTEGER NOT NULL REFERENCES dim_competitions (competition_id),
    season_id      INTEGER NOT NULL REFERENCES dim_seasons (season_id),
    match_date     DATE,
    home_team      TEXT,
    away_team      TEXT,
    home_score     SMALLINT,
    away_score     SMALLINT,
    stage          TEXT,
    stadium        TEXT,
    referee        TEXT
);

CREATE TABLE IF NOT EXISTS dim_teams (
    team_id   INTEGER PRIMARY KEY,
    team_name TEXT NOT NULL,
    country   TEXT
);

CREATE TABLE IF NOT EXISTS dim_players (
    player_id   INTEGER PRIMARY KEY,
    player_name TEXT NOT NULL,
    nationality TEXT,
    position    TEXT
);

-- ─── Ingestion Tracking ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ingested_matches (
    match_id      INTEGER PRIMARY KEY,
    competition_id INTEGER NOT NULL,
    ingested_at   TIMESTAMP DEFAULT NOW(),
    event_count   INTEGER,
    status        TEXT     DEFAULT 'success'
);

-- ─── Partitioned Fact Table (LIST on competition_id) ────────────────────────

CREATE TABLE IF NOT EXISTS fact_events (
    event_id        UUID     NOT NULL,
    match_id        INTEGER  NOT NULL,
    competition_id  INTEGER  NOT NULL,
    team_id         INTEGER,
    player_id       INTEGER,
    event_type      TEXT,
    minute          SMALLINT,
    second          SMALLINT,
    location_x      FLOAT,
    location_y      FLOAT,
    end_location_x  FLOAT,
    end_location_y  FLOAT,
    outcome         TEXT,
    under_pressure  BOOLEAN  DEFAULT FALSE,
    xt_value        FLOAT,
    xp_value        FLOAT,
    xg_value        FLOAT,
    raw_json        JSONB,
    PRIMARY KEY (event_id, competition_id)
) PARTITION BY LIST (competition_id);

-- Default partition catches any competition not yet explicitly partitioned.
CREATE TABLE IF NOT EXISTS fact_events_default
    PARTITION OF fact_events DEFAULT;

-- ─── Partition Manager ──────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION create_competition_partition(p_id INTEGER)
RETURNS VOID AS $$
DECLARE
    tname TEXT := 'fact_events_comp_' || p_id;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = tname) THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF fact_events FOR VALUES IN (%s)',
            tname, p_id
        );
        RAISE NOTICE 'Partition created: %', tname;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- ─── Analytical Surfaces ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS xt_surface (
    grid_col  SMALLINT,
    grid_row  SMALLINT,
    xt_value  FLOAT,
    PRIMARY KEY (grid_col, grid_row)
);

CREATE TABLE IF NOT EXISTS set_piece_clusters (
    cluster_id      SERIAL PRIMARY KEY,
    event_type      TEXT,
    cluster_label   INTEGER,
    center_x        FLOAT,
    center_y        FLOAT,
    member_count    INTEGER,
    trained_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS model_registry (
    model_id      SERIAL PRIMARY KEY,
    model_name    TEXT NOT NULL,
    version       TEXT,
    trained_at    TIMESTAMP DEFAULT NOW(),
    metrics       JSONB,
    artifact_path TEXT
);

-- ─── Analytics Marts Schema ─────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS analytics_marts;

-- ─── Indexes ────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_fe_match       ON fact_events (match_id, competition_id);
CREATE INDEX IF NOT EXISTS idx_fe_player      ON fact_events (player_id, competition_id);
CREATE INDEX IF NOT EXISTS idx_fe_type        ON fact_events (event_type, competition_id);
CREATE INDEX IF NOT EXISTS idx_fe_xt          ON fact_events (xt_value, competition_id)
    WHERE xt_value IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fe_xp          ON fact_events (xp_value, competition_id)
    WHERE xp_value IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fe_xg          ON fact_events (xg_value, competition_id)
    WHERE xg_value IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ingested_comp  ON ingested_matches (competition_id);
