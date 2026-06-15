"""
visualise.py — Portfolio-grade static visualisations from live pipeline data.

Generates five PNG exports from the analytics database:
  1. xT surface heatmap          (xt_surface table)
  2. Shot map with xG bubbles    (analytics_silver.silver_shots)
  3. Team xG vs goals bar chart  (mv_team_xg)
  4. Set-piece cluster centroids (set_piece_clusters)
  5. Player xP ranking           (analytics_analytics_marts.mart_player_metrics)

MPLBACKEND=Agg must be set by the caller for headless environments.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from mplsoccer import Pitch, VerticalPitch
from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_user = os.environ.get("POSTGRES_USER", "analytics")
_pass = os.environ.get("POSTGRES_PASSWORD", "analytics")
_host = os.environ.get("POSTGRES_HOST", "postgres")
_db = os.environ.get("POSTGRES_DB", "football_db")
DB_URL = f"postgresql+psycopg2://{_user}:{_pass}@{_host}:5432/{_db}"

OUT_DIR = Path(os.environ.get("VIZ_OUTPUT_DIR", "data/visualisations"))

_BG = "#0d1b2a"
_ACCENT = "#e8c842"


def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


def _apply_dark_axes(ax) -> None:
    ax.set_facecolor(_BG)
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_edgecolor("#334455")
    ax.grid(axis="x", color="#334455", linestyle="--", linewidth=0.5, alpha=0.6)


# ─── 1. xT Surface Heatmap ───────────────────────────────────────────────────


def plot_xt_surface(engine) -> Path | None:
    with engine.connect() as conn:
        df = pd.read_sql(
            text(
                "SELECT grid_col, grid_row, xt_value FROM xt_surface ORDER BY grid_row, grid_col"
            ),
            conn,
        )

    if df.empty:
        log.warning("xt_surface empty — skipping xT heatmap")
        return None

    grid = df.pivot(index="grid_row", columns="grid_col", values="xt_value").values

    fig, ax = plt.subplots(figsize=(12, 8), facecolor=_BG)
    ax.set_facecolor(_BG)

    pitch = Pitch(
        pitch_type="custom",
        pitch_length=105,
        pitch_width=68,
        pitch_color=_BG,
        line_color="white",
        line_zorder=2,
    )
    pitch.draw(ax=ax)

    ax.imshow(
        grid,
        extent=[0, 105, 0, 68],
        origin="lower",
        cmap="YlOrRd",
        alpha=0.75,
        aspect="auto",
        zorder=1,
    )

    sm = plt.cm.ScalarMappable(
        cmap="YlOrRd", norm=plt.Normalize(grid.min(), grid.max())
    )
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, fraction=0.025, pad=0.02)
    cbar.set_label("xT Value", color="white", fontsize=11)
    cbar.ax.yaxis.set_tick_params(color="white")
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color="white")

    ax.set_title(
        "Expected Threat (xT) Surface — 16×12 Grid | 105×68 m Universal Pitch",
        color="white",
        fontsize=13,
        fontweight="bold",
        pad=12,
    )
    ax.axis("off")

    out = OUT_DIR / "xt_surface_heatmap.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    log.info("Saved: %s", out)
    return out


# ─── 2. Shot Map with xG bubbles ─────────────────────────────────────────────


def plot_shot_map(engine) -> Path | None:
    q = text(
        """
        SELECT ss.location_x, ss.location_y, ss.is_goal, fe.xg_value
        FROM   analytics_silver.silver_shots ss
        JOIN   fact_events fe ON ss.event_id = fe.event_id
        WHERE  fe.xg_value IS NOT NULL
        LIMIT  4000
    """
    )
    with engine.connect() as conn:
        df = pd.read_sql(q, conn)

    if df.empty:
        log.warning("No shot data with xG — skipping shot map")
        return None

    df["xg_value"] = pd.to_numeric(df["xg_value"], errors="coerce").fillna(0.0)
    goals = df[df["is_goal"] == 1]
    saves = df[df["is_goal"] == 0]

    pitch = VerticalPitch(
        pitch_type="custom",
        pitch_length=105,
        pitch_width=68,
        half=True,
        pitch_color=_BG,
        line_color="white",
        goal_type="box",
        linewidth=1,
    )
    fig, ax = pitch.draw(figsize=(8, 10), constrained_layout=True)
    fig.set_facecolor(_BG)

    sc = pitch.scatter(
        saves["location_x"],
        saves["location_y"],
        s=saves["xg_value"] * 900 + 18,
        c=saves["xg_value"],
        cmap="Blues",
        alpha=0.6,
        linewidths=0.4,
        edgecolors="white",
        zorder=3,
        ax=ax,
    )
    pitch.scatter(
        goals["location_x"],
        goals["location_y"],
        s=goals["xg_value"] * 900 + 45,
        c=_ACCENT,
        alpha=0.9,
        linewidths=0.8,
        edgecolors="white",
        zorder=4,
        marker="*",
        ax=ax,
    )

    cbar = fig.colorbar(sc, ax=ax, fraction=0.030, pad=0.05, shrink=0.55)
    cbar.set_label("xG Value", color="white", fontsize=10)
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color="white")
    cbar.ax.yaxis.set_tick_params(color="white")

    ax.set_title(
        f"Shot Map  ({len(df):,} shots · {len(goals)} goals)\n"
        "Bubble size ∝ xG  |  ★ = Goal",
        color="white",
        fontsize=12,
        fontweight="bold",
        pad=10,
    )

    out = OUT_DIR / "shot_map_xg.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    log.info("Saved: %s", out)
    return out


# ─── 3. Team xG vs Goals ─────────────────────────────────────────────────────


def plot_team_xg(engine) -> Path | None:
    q = text(
        """
        SELECT team_name, total_shots, total_xg, goals_scored, conversion_rate
        FROM   mv_team_xg
        ORDER  BY total_xg DESC
        LIMIT  20
    """
    )
    with engine.connect() as conn:
        df = pd.read_sql(q, conn)

    if df.empty:
        log.warning("mv_team_xg empty — skipping team xG chart")
        return None

    for col in ("total_xg", "goals_scored"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df = df.sort_values("total_xg", ascending=True)

    fig, ax = plt.subplots(figsize=(12, max(6, len(df) * 0.48)), facecolor=_BG)
    _apply_dark_axes(ax)

    y = np.arange(len(df))
    h = 0.35
    ax.barh(y + h / 2, df["total_xg"], h, label="Total xG", color="#4a90d9", alpha=0.85)
    ax.barh(
        y - h / 2,
        df["goals_scored"],
        h,
        label="Goals Scored",
        color=_ACCENT,
        alpha=0.85,
    )

    ax.set_yticks(y)
    ax.set_yticklabels(df["team_name"], color="white", fontsize=8)
    ax.set_xlabel("Count", color="white")
    ax.set_title(
        "Team xG vs Goals Scored — Top 20 by Total xG",
        color="white",
        fontsize=13,
        fontweight="bold",
        pad=12,
    )
    ax.legend(facecolor="#1e2d3d", labelcolor="white", framealpha=0.8)

    out = OUT_DIR / "team_xg_comparison.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    log.info("Saved: %s", out)
    return out


# ─── 4. Set-Piece Cluster Map ─────────────────────────────────────────────────


def plot_set_piece_clusters(engine) -> Path | None:
    q = text(
        """
        SELECT event_type, cluster_label, center_x, center_y, member_count
        FROM   set_piece_clusters
        ORDER  BY event_type, cluster_label
    """
    )
    with engine.connect() as conn:
        df = pd.read_sql(q, conn)

    if df.empty:
        log.warning("set_piece_clusters empty — skipping cluster map")
        return None

    for col in ("center_x", "center_y", "member_count"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    types = df["event_type"].unique().tolist()
    ncols = min(len(types), 2)
    nrows = (len(types) + ncols - 1) // ncols

    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(14, 7 * nrows),
        facecolor=_BG,
        squeeze=False,
    )
    fig.suptitle(
        "Set-Piece Delivery Clusters  (K-Means k=6 · 105×68 m pitch)",
        color="white",
        fontsize=13,
        fontweight="bold",
        y=1.01,
    )

    cluster_colors = ["#e8c842", "#4a90d9", "#e74c3c", "#2ecc71", "#9b59b6", "#e67e22"]

    for idx, etype in enumerate(types):
        row, col = divmod(idx, ncols)
        ax = axes[row][col]

        pitch = Pitch(
            pitch_type="custom",
            pitch_length=105,
            pitch_width=68,
            pitch_color=_BG,
            line_color="white",
            linewidth=1,
        )
        pitch.draw(ax=ax)

        sub = df[df["event_type"] == etype]
        for _, r in sub.iterrows():
            c = cluster_colors[int(r["cluster_label"]) % len(cluster_colors)]
            sz = max(220, float(r["member_count"]) * 0.85)
            ax.scatter(
                r["center_x"],
                r["center_y"],
                s=sz,
                c=c,
                alpha=0.88,
                edgecolors="white",
                linewidths=1.2,
                zorder=4,
            )
            ax.annotate(
                f"C{int(r['cluster_label'])}\n{int(r['member_count'])}",
                (r["center_x"], r["center_y"]),
                color="white",
                fontsize=7,
                ha="center",
                va="center",
                fontweight="bold",
                zorder=5,
            )

        ax.set_title(
            f"{etype} Clusters", color="white", fontsize=11, fontweight="bold", pad=8
        )
        ax.axis("off")

    for idx in range(len(types), nrows * ncols):
        row, col = divmod(idx, ncols)
        axes[row][col].set_visible(False)

    out = OUT_DIR / "set_piece_clusters.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    log.info("Saved: %s", out)
    return out


# ─── 5. Player xP Ranking ─────────────────────────────────────────────────────


def plot_player_xp(engine) -> Path | None:
    q = text(
        """
        SELECT player_name, team_name, avg_xp, total_passes, pass_completion_pct
        FROM   analytics_analytics_marts.mart_player_metrics
        WHERE  avg_xp IS NOT NULL
          AND  total_passes >= 50
        ORDER  BY avg_xp DESC
        LIMIT  20
    """
    )
    with engine.connect() as conn:
        df = pd.read_sql(q, conn)

    if df.empty:
        log.warning(
            "mart_player_metrics has no qualifying rows — skipping player xP chart"
        )
        return None

    df["avg_xp"] = pd.to_numeric(df["avg_xp"], errors="coerce")
    df = df.dropna(subset=["avg_xp"]).sort_values("avg_xp", ascending=True)
    df["label"] = df["player_name"] + "  (" + df["team_name"] + ")"

    norm = plt.Normalize(df["avg_xp"].min(), df["avg_xp"].max())
    bar_colors = [plt.cm.RdYlGn(norm(v)) for v in df["avg_xp"]]

    fig, ax = plt.subplots(figsize=(12, max(6, len(df) * 0.48)), facecolor=_BG)
    _apply_dark_axes(ax)

    bars = ax.barh(df["label"], df["avg_xp"], color=bar_colors, alpha=0.88)

    for bar, val in zip(bars, df["avg_xp"]):
        ax.text(
            bar.get_width() + 0.002,
            bar.get_y() + bar.get_height() / 2,
            f"{val:.3f}",
            va="center",
            color="white",
            fontsize=8,
        )

    ax.set_xlim(0, df["avg_xp"].max() * 1.12)
    ax.set_xlabel("Average xP (pass success probability)", color="white")
    ax.set_yticklabels(df["label"], color="white", fontsize=8)
    ax.set_title(
        "Top 20 Players by xP — XGBoost Pass Quality Model  (≥50 passes)",
        color="white",
        fontsize=13,
        fontweight="bold",
        pad=12,
    )

    out = OUT_DIR / "player_xp_ranking.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    log.info("Saved: %s", out)
    return out


# ─── Entry point ──────────────────────────────────────────────────────────────


def run() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    engine = get_engine()

    chart_fns = {
        "xT Surface Heatmap": lambda: plot_xt_surface(engine),
        "Shot Map (xG)": lambda: plot_shot_map(engine),
        "Team xG vs Goals": lambda: plot_team_xg(engine),
        "Set-Piece Clusters": lambda: plot_set_piece_clusters(engine),
        "Player xP Ranking": lambda: plot_player_xp(engine),
    }

    results: dict[str, Path | None] = {}
    for name, fn in chart_fns.items():
        try:
            results[name] = fn()
        except Exception:
            log.exception("Chart '%s' failed — continuing", name)
            results[name] = None

    generated = [v for v in results.values() if v is not None]
    log.info("=" * 60)
    log.info(
        "Visualisation complete: %d / %d charts generated", len(generated), len(results)
    )
    for name, path in results.items():
        log.info("  %-28s → %s", name, path or "FAILED/SKIPPED")
    log.info("=" * 60)

    if not generated:
        log.error(
            "No charts generated — check database connection and data availability"
        )
        sys.exit(1)


if __name__ == "__main__":
    run()
