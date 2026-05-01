"""
PDF match report generator.

Produces a multi-page PDF per match containing:
  Page 1 — xT heatmap (16x12 grid overlay on pitch)
  Page 2 — Pass network for each team (node = player, edge weight = pass count)
  Page 3 — Top-15 xT event scatter on pitch
  Page 4 — Player xT/xP bar charts (top 10 per team)

Output: /opt/airflow/reports/match_{match_id}.pdf
"""

import logging
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from mplsoccer import Pitch, VerticalPitch
from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DB_URL = (
    f"postgresql+psycopg2://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:{os.environ.get('POSTGRES_PORT', '5432')}"
    f"/{os.environ['POSTGRES_DB']}"
)

REPORT_DIR = "/opt/airflow/reports"
GRID_COLS  = 16
GRID_ROWS  = 12


def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


# ─── Data queries ─────────────────────────────────────────────────────────────

def fetch_xt_surface(engine) -> np.ndarray:
    with engine.connect() as conn:
        df = pd.read_sql(
            text("SELECT grid_col, grid_row, xt_value FROM xt_surface ORDER BY grid_row, grid_col"),
            conn,
        )
    grid = np.zeros((GRID_ROWS, GRID_COLS))
    for _, row in df.iterrows():
        grid[int(row["grid_row"]), int(row["grid_col"])] = row["xt_value"]
    return grid


def fetch_match_events(engine, match_id: int) -> pd.DataFrame:
    q = text("""
        SELECT fe.event_id, fe.event_type, fe.minute, fe.second,
               fe.location_x, fe.location_y,
               fe.end_location_x, fe.end_location_y,
               fe.xt_value, fe.xp_value, fe.under_pressure,
               dp.player_name, dt.team_name, fe.team_id
        FROM fact_events fe
        LEFT JOIN dim_players dp ON fe.player_id = dp.player_id
        LEFT JOIN dim_teams   dt ON fe.team_id   = dt.team_id
        WHERE fe.match_id = :mid
    """)
    with engine.connect() as conn:
        return pd.read_sql(q, conn, params={"mid": match_id})


def fetch_match_meta(engine, match_id: int) -> dict:
    q = text("""
        SELECT home_team, away_team, home_score, away_score, match_date
        FROM dim_matches WHERE match_id = :mid
    """)
    with engine.connect() as conn:
        row = conn.execute(q, {"mid": match_id}).fetchone()
    return dict(row._mapping) if row else {}


# ─── Plot helpers ─────────────────────────────────────────────────────────────

def _fig_title(fig, text_: str) -> None:
    fig.suptitle(text_, fontsize=11, fontweight="bold", y=0.98)


def plot_xt_heatmap(grid: np.ndarray, meta: dict) -> plt.Figure:
    pitch = Pitch(pitch_type="statsbomb", pitch_color="#1a1a2e", line_color="#ffffff")
    fig, ax = pitch.draw(figsize=(12, 8))
    fig.set_facecolor("#1a1a2e")

    cell_w = 120 / GRID_COLS
    cell_h = 80 / GRID_ROWS

    im = ax.imshow(
        grid,
        extent=[0, 120, 80, 0],
        aspect="auto",
        cmap="RdYlGn",
        alpha=0.75,
        vmin=0,
        vmax=grid.max() or 1,
    )
    plt.colorbar(im, ax=ax, fraction=0.03, pad=0.02, label="xT value")

    title = (f"{meta.get('home_team', '')} vs {meta.get('away_team', '')} "
             f"— xT Surface ({meta.get('match_date', '')})")
    _fig_title(fig, title)
    return fig


def plot_pass_network(events: pd.DataFrame, team_name: str) -> plt.Figure:
    passes = events[
        (events["event_type"] == "Pass")
        & (events["team_name"] == team_name)
        & events["location_x"].notna()
        & events["end_location_x"].notna()
    ].copy()

    if passes.empty:
        fig, ax = plt.subplots(figsize=(10, 7))
        ax.text(0.5, 0.5, f"No pass data for {team_name}", ha="center", transform=ax.transAxes)
        return fig

    player_avg = passes.groupby("player_name").agg(
        avg_x=("location_x", "mean"),
        avg_y=("location_y", "mean"),
        count=("event_id", "count"),
    ).reset_index()

    pitch = Pitch(pitch_type="statsbomb", pitch_color="#0d1b2a", line_color="#aaaaaa")
    fig, ax = pitch.draw(figsize=(12, 8))
    fig.set_facecolor("#0d1b2a")

    for _, row in player_avg.iterrows():
        size = np.clip(row["count"] * 3, 50, 600)
        ax.scatter(row["avg_x"], row["avg_y"], s=size, color="#e63946", zorder=3, alpha=0.9)
        ax.text(row["avg_x"], row["avg_y"] - 3, row["player_name"].split()[-1],
                fontsize=6, color="white", ha="center", va="top")

    _fig_title(fig, f"{team_name} — Pass Network")
    return fig


def plot_top_xt_events(events: pd.DataFrame, meta: dict) -> plt.Figure:
    top = (
        events[events["xt_value"].notna()]
        .nlargest(15, "xt_value")
    )

    pitch = Pitch(pitch_type="statsbomb", pitch_color="#1a1a2e", line_color="#ffffff")
    fig, ax = pitch.draw(figsize=(12, 8))
    fig.set_facecolor("#1a1a2e")

    sc = ax.scatter(
        top["location_x"], top["location_y"],
        c=top["xt_value"], cmap="plasma",
        s=120, zorder=3, alpha=0.9,
    )
    for _, row in top.iterrows():
        if pd.notna(row["end_location_x"]):
            ax.annotate(
                "",
                xy=(row["end_location_x"], row["end_location_y"]),
                xytext=(row["location_x"], row["location_y"]),
                arrowprops={"arrowstyle": "->", "color": "white", "lw": 0.8},
            )
    plt.colorbar(sc, ax=ax, fraction=0.03, pad=0.02, label="xT")
    _fig_title(fig, "Top 15 xT Actions")
    return fig


def plot_player_bars(events: pd.DataFrame) -> plt.Figure:
    player_xt = (
        events[events["xt_value"].notna()]
        .groupby(["player_name", "team_name"])["xt_value"]
        .sum()
        .reset_index()
        .nlargest(10, "xt_value")
    )

    fig, ax = plt.subplots(figsize=(10, 6))
    fig.set_facecolor("#1a1a2e")
    ax.set_facecolor("#1a1a2e")

    colors = ["#e63946" if i % 2 == 0 else "#457b9d" for i in range(len(player_xt))]
    bars   = ax.barh(player_xt["player_name"], player_xt["xt_value"], color=colors)

    ax.set_xlabel("Total xT", color="white")
    ax.tick_params(colors="white")
    ax.spines[:].set_color("#444444")
    for bar, val in zip(bars, player_xt["xt_value"]):
        ax.text(val + 0.002, bar.get_y() + bar.get_height() / 2,
                f"{val:.3f}", va="center", color="white", fontsize=8)

    _fig_title(fig, "Top 10 Players by Total xT")
    return fig


# ─── Entry point ─────────────────────────────────────────────────────────────

def run(match_id: int) -> str:
    engine = get_engine()
    os.makedirs(REPORT_DIR, exist_ok=True)

    meta   = fetch_match_meta(engine, match_id)
    events = fetch_match_events(engine, match_id)
    grid   = fetch_xt_surface(engine)

    teams  = events["team_name"].dropna().unique()
    path   = os.path.join(REPORT_DIR, f"match_{match_id}.pdf")

    with PdfPages(path) as pdf:
        for fig in [
            plot_xt_heatmap(grid, meta),
            *(plot_pass_network(events, t) for t in teams),
            plot_top_xt_events(events, meta),
            plot_player_bars(events),
        ]:
            pdf.savefig(fig, bbox_inches="tight", facecolor=fig.get_facecolor())
            plt.close(fig)

    log.info("Report written: %s", path)
    return path


if __name__ == "__main__":
    import sys
    run(int(sys.argv[1]))
