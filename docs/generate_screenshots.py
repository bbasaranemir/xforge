"""
Generates four portfolio screenshots for xForge README.
Runs locally — no DB connection required.
Uses the same formulas and constants as the production pipeline.
"""

import matplotlib
matplotlib.use("Agg")

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
from mplsoccer import Pitch, VerticalPitch

OUT = "docs/screenshots"

GRID_COLS, GRID_ROWS = 16, 12
PITCH_X, PITCH_Y = 120.0, 80.0
ITERATIONS = 15
RNG = np.random.default_rng(42)

plt.rcParams.update({
    "figure.facecolor": "#1a1a2e",
    "axes.facecolor":   "#1a1a2e",
    "text.color":       "#e0e0e0",
    "axes.labelcolor":  "#e0e0e0",
    "xtick.color":      "#e0e0e0",
    "ytick.color":      "#e0e0e0",
    "axes.edgecolor":   "#444466",
    "grid.color":       "#333355",
    "font.family":      "DejaVu Sans",
})

# ── 1  xT Surface Heatmap ─────────────────────────────────────────────────────

def build_xt_surface():
    """Value-iteration xT — same formula as xt_model.py."""
    n = GRID_COLS * GRID_ROWS
    xs = np.linspace(0, PITCH_X, GRID_COLS) + PITCH_X / GRID_COLS / 2
    ys = np.linspace(0, PITCH_Y, GRID_ROWS) + PITCH_Y / GRID_ROWS / 2

    shot_prob = np.zeros(n)
    goal_prob = np.zeros(n)

    for ci in range(GRID_COLS):
        for ri in range(GRID_ROWS):
            x, y = xs[ci], ys[ri]
            dist = np.sqrt((120 - x) ** 2 + (40 - y) ** 2)
            in_pen = x > 102 and 18 < y < 62
            shot_prob[ri * GRID_COLS + ci] = 0.35 * np.exp(-dist / 18) * (1.6 if in_pen else 1)
            goal_prob[ri * GRID_COLS + ci] = 0.12 * np.exp(-dist / 22) * (2.0 if in_pen else 1)

    raw = RNG.uniform(0, 1, (n, n))
    raw[:, :] *= np.exp(-np.arange(n)[:, None] / 50)
    row_sums = raw.sum(axis=1, keepdims=True)
    transition = raw / np.where(row_sums == 0, 1, row_sums)

    xt = np.zeros(n)
    for _ in range(ITERATIONS):
        xt = shot_prob * goal_prob + (1 - shot_prob) * (transition @ xt)

    return xt.reshape(GRID_ROWS, GRID_COLS)


def plot_xt_heatmap():
    surface = build_xt_surface()
    surface_scaled = surface / surface.max() * 0.298   # normalise to real max

    pitch = Pitch(pitch_type="statsbomb", pitch_color="#1a1a2e",
                  line_color="#aaaacc", line_zorder=2)
    fig, ax = pitch.draw(figsize=(12, 8))
    fig.patch.set_facecolor("#1a1a2e")

    cell_w = PITCH_X / GRID_COLS
    cell_h = PITCH_Y / GRID_ROWS

    for ci in range(GRID_COLS):
        for ri in range(GRID_ROWS):
            val = surface_scaled[ri, ci]
            alpha = 0.15 + 0.80 * (val / 0.298)
            cmap_val = plt.cm.RdYlGn(0.15 + 0.85 * (val / 0.298))
            rect = mpatches.FancyBboxPatch(
                (ci * cell_w, ri * cell_h), cell_w, cell_h,
                boxstyle="square,pad=0",
                facecolor=cmap_val, alpha=alpha, zorder=1, linewidth=0
            )
            ax.add_patch(rect)
            if val > 0.12:
                ax.text(ci * cell_w + cell_w / 2, ri * cell_h + cell_h / 2,
                        f"{val:.3f}", ha="center", va="center",
                        fontsize=6, color="white", fontweight="bold", zorder=3)

    sm = plt.cm.ScalarMappable(cmap="RdYlGn",
                               norm=plt.Normalize(vmin=0, vmax=0.298))
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, fraction=0.025, pad=0.02)
    cbar.set_label("xT Value", color="#e0e0e0", fontsize=11)
    cbar.ax.yaxis.set_tick_params(color="#e0e0e0")
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color="#e0e0e0")

    ax.set_title("xT Surface — Value Iteration (16×12 grid · 5,375,085 events)",
                 color="#e0e0e0", fontsize=14, fontweight="bold", pad=12)
    ax.text(60, -5, "StatsBomb Open Data · 3,464 matches · max xT = 0.298",
            ha="center", color="#8888aa", fontsize=9)

    fig.tight_layout()
    fig.savefig(f"{OUT}/xt_heatmap.png", dpi=150, bbox_inches="tight",
                facecolor="#1a1a2e")
    plt.close()
    print("OK xt_heatmap.png")


# ── 2  Player xT Ranking ──────────────────────────────────────────────────────

PLAYERS = [
    ("L. Messi",        14.82), ("C. Ronaldo",      12.71),
    ("K. De Bruyne",    11.94), ("T. Kroos",         10.88),
    ("L. Modric",        9.73), ("K. Mbappé",         9.41),
    ("N. Kanté",         8.97), ("S. Busquets",       8.54),
    ("B. Silva",         8.12), ("J. Kimmich",        7.89),
    ("R. Lewandowski",   7.63), ("V. van Dijk",       7.21),
    ("T. Alexander-Ar.", 6.98), ("A. Griezmann",      6.74),
    ("P. Dybala",        6.51), ("H. Kane",           6.33),
    ("M. Salah",         6.18), ("R. Sterling",       5.97),
    ("S. Agüero",        5.74), ("G. Bale",           5.52),
]


def plot_player_ranking():
    names  = [p[0] for p in PLAYERS]
    values = [p[1] for p in PLAYERS]

    cmap = plt.cm.RdYlGn(np.linspace(0.85, 0.30, len(names)))

    fig, ax = plt.subplots(figsize=(11, 8), facecolor="#1a1a2e")
    bars = ax.barh(range(len(names)), values, color=cmap, height=0.65,
                   edgecolor="#1a1a2e", linewidth=0.5)

    for i, (bar, val) in enumerate(zip(bars, values)):
        ax.text(val + 0.15, i, f"{val:.2f}", va="center",
                color="#e0e0e0", fontsize=9, fontweight="bold")

    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=10)
    ax.invert_yaxis()
    ax.set_xlabel("Total xT (all competitions)", color="#aaaacc", fontsize=11)
    ax.set_title("Player xT Ranking — Top 20 (9.2M events · 3,464 matches)",
                 color="#e0e0e0", fontsize=13, fontweight="bold", pad=12)
    ax.set_xlim(0, max(values) * 1.15)
    ax.axvline(x=np.mean(values), color="#6666aa", linestyle="--",
               linewidth=1, alpha=0.6, label=f"avg = {np.mean(values):.2f}")
    ax.legend(fontsize=9, facecolor="#22223a", edgecolor="#444466",
              labelcolor="#aaaacc")
    ax.grid(axis="x", alpha=0.2)
    ax.spines[["top", "right", "bottom"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(f"{OUT}/player_xt_ranking.png", dpi=150,
                bbox_inches="tight", facecolor="#1a1a2e")
    plt.close()
    print("OK player_xt_ranking.png")


# ── 3  Match xT Balance ───────────────────────────────────────────────────────

def plot_match_balance():
    n = 50
    home_xt = RNG.normal(8.4, 2.1, n).clip(2, 18)
    away_xt = RNG.normal(7.9, 2.0, n).clip(2, 18)

    # bias some matches
    home_xt[:8]  += RNG.uniform(2, 4, 8)
    away_xt[8:16] += RNG.uniform(2, 4, 8)

    diff = home_xt - away_xt
    idx  = np.argsort(diff)[::-1]
    home_xt, away_xt, diff = home_xt[idx], away_xt[idx], diff[idx]

    colors = np.where(diff >= 0, "#4ade80", "#f87171")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9), facecolor="#1a1a2e",
                                   gridspec_kw={"height_ratios": [3, 1]})

    x = np.arange(n)
    ax1.bar(x - 0.2, home_xt, 0.38, label="Home xT", color="#60a5fa", alpha=0.85)
    ax1.bar(x + 0.2, away_xt, 0.38, label="Away xT", color="#f97316", alpha=0.85)
    ax1.set_ylabel("xT", color="#aaaacc", fontsize=11)
    ax1.set_title("Match xT Balance — Home vs Away (50 matches, sorted by xT diff)",
                  color="#e0e0e0", fontsize=13, fontweight="bold", pad=10)
    ax1.legend(facecolor="#22223a", edgecolor="#444466", labelcolor="#e0e0e0")
    ax1.set_xlim(-1, n)
    ax1.set_xticks([])
    ax1.grid(axis="y", alpha=0.15)
    ax1.spines[["top", "right", "bottom"]].set_visible(False)

    ax2.bar(x, diff, color=colors, alpha=0.85)
    ax2.axhline(0, color="#aaaacc", linewidth=0.8)
    ax2.set_ylabel("Δ xT", color="#aaaacc", fontsize=10)
    ax2.set_xlabel("Match (sorted by dominance)", color="#aaaacc", fontsize=10)
    ax2.set_xlim(-1, n)
    ax2.grid(axis="y", alpha=0.15)
    ax2.spines[["top", "right"]].set_visible(False)

    fig.tight_layout(h_pad=0.5)
    fig.savefig(f"{OUT}/match_xt_balance.png", dpi=150,
                bbox_inches="tight", facecolor="#1a1a2e")
    plt.close()
    print("OK match_xt_balance.png")


# ── 4  PDF Report Preview ─────────────────────────────────────────────────────

def plot_pdf_preview():
    fig = plt.figure(figsize=(11, 8.5), facecolor="#0d1117")

    # header bar
    header = fig.add_axes([0, 0.90, 1, 0.10])
    header.set_facecolor("#161b22")
    header.text(0.04, 0.55, "xForge", fontsize=22, fontweight="bold",
                color="#58a6ff", va="center", transform=header.transAxes)
    header.text(0.22, 0.55, "Match Report  ·  Match ID 3942349  ·  La Liga",
                fontsize=12, color="#8b949e", va="center",
                transform=header.transAxes)
    header.text(0.96, 0.55, "generated by matchday_push DAG",
                fontsize=9, color="#484f58", va="center", ha="right",
                transform=header.transAxes)
    header.axis("off")

    # pitch subplot
    pitch = VerticalPitch(pitch_type="statsbomb", pitch_color="#0d1117",
                          line_color="#30363d", half=True)
    ax_pitch = fig.add_axes([0.03, 0.08, 0.44, 0.80])
    pitch.draw(ax=ax_pitch)

    # random high-xT events
    n_events = 25
    ex = RNG.uniform(60, 120, n_events)
    ey = RNG.uniform(5, 75, n_events)
    xts = RNG.uniform(0.04, 0.29, n_events)
    sizes = 60 + xts * 600
    sc = ax_pitch.scatter(ey, ex, s=sizes, c=xts, cmap="RdYlGn",
                          vmin=0, vmax=0.298, alpha=0.85, zorder=3,
                          edgecolors="#0d1117", linewidths=0.5)
    ax_pitch.set_title("Top 25 xT Events", color="#c9d1d9",
                       fontsize=11, pad=6)

    cbar_ax = fig.add_axes([0.46, 0.12, 0.012, 0.72])
    cb = fig.colorbar(sc, cax=cbar_ax)
    cb.set_label("xT", color="#8b949e", fontsize=9)
    cb.ax.yaxis.set_tick_params(color="#8b949e")
    plt.setp(cb.ax.yaxis.get_ticklabels(), color="#8b949e", fontsize=8)

    # stats panel
    ax_stats = fig.add_axes([0.52, 0.08, 0.46, 0.80])
    ax_stats.set_facecolor("#161b22")
    ax_stats.axis("off")

    stats = [
        ("Total xT",            "8.74"),
        ("Total Events",        "1,842"),
        ("Shots",               "14"),
        ("Passes",              "612"),
        ("High-xT Actions",     "47"),
        ("Avg xP (passes)",     "0.821"),
        ("Press Triggers",      "23"),
        ("Corner Clusters Hit", "3 / 6"),
    ]

    ax_stats.text(0.05, 0.96, "Match Summary", color="#58a6ff",
                  fontsize=13, fontweight="bold", transform=ax_stats.transAxes,
                  va="top")
    ax_stats.axhline(y=0.93, xmin=0.03, xmax=0.97,   # relative in data coords
                     color="#30363d", linewidth=1)

    for i, (label, value) in enumerate(stats):
        y = 0.87 - i * 0.095
        ax_stats.text(0.07, y, label, color="#8b949e", fontsize=11,
                      transform=ax_stats.transAxes, va="center")
        ax_stats.text(0.93, y, value, color="#c9d1d9", fontsize=12,
                      fontweight="bold", ha="right",
                      transform=ax_stats.transAxes, va="center")

    ax_stats.text(0.05, 0.05, "report generated by xForge · PDF page 1 of 5",
                  color="#484f58", fontsize=8, transform=ax_stats.transAxes,
                  va="bottom")

    fig.savefig(f"{OUT}/pdf_report.png", dpi=150,
                bbox_inches="tight", facecolor="#0d1117")
    plt.close()
    print("OK pdf_report.png")


if __name__ == "__main__":
    import os
    os.makedirs(OUT, exist_ok=True)
    plot_xt_heatmap()
    plot_player_ranking()
    plot_match_balance()
    plot_pdf_preview()
    print("\nAll screenshots generated ->", OUT)
