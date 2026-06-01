"""
setup_superset.py — Fully autonomous Superset bootstrap for xForge.

Creates (idempotent — safe to re-run):
  1. PostgreSQL database connection
  2. Virtual SQL datasets (one per chart)
  3. Seven analytics charts
  4. "Matchday Analytics" dashboard (published)

Usage:
    python /opt/airflow/scripts/setup_superset.py
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

# ── Logging ───────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Config ────────────────────────────────────────────────────────────────────
SUPERSET_URL = os.environ.get("SUPERSET_URL", "http://superset:8088")
ADMIN_USER   = os.environ.get("SUPERSET_USER",     "admin")
ADMIN_PASS   = os.environ.get("SUPERSET_PASSWORD", "admin123")

_pg_user = os.environ.get("POSTGRES_USER",     "analytics")
_pg_pass = os.environ.get("POSTGRES_PASSWORD", "analytics123")
_pg_host = os.environ.get("POSTGRES_HOST",     "postgres")
_pg_db   = os.environ.get("FOOTBALL_DB",       "football_analytics")

DB_NAME     = "xForge — Football Analytics"
DB_CONN_STR = f"postgresql+psycopg2://{_pg_user}:{_pg_pass}@{_pg_host}:5432/{_pg_db}"
DASHBOARD_TITLE = "Matchday Analytics"


# ── Metric helper ─────────────────────────────────────────────────────────────
def _m(col: str, label: str | None = None) -> dict:
    """Adhoc SQL metric referencing a pre-computed column."""
    return {
        "expressionType": "SQL",
        "sqlExpression": col,
        "label": label or col,
        "hasCustomLabel": bool(label),
    }


# ── Chart definitions ─────────────────────────────────────────────────────────
CHART_DEFS: list[dict[str, Any]] = [
    # 1 ── xT Surface Heatmap
    {
        "name": "xT Surface Heatmap",
        "sql": (
            "SELECT grid_col, grid_row, "
            "ROUND(xt_value::numeric, 4) AS xt_value "
            "FROM xt_surface ORDER BY grid_row, grid_col"
        ),
        "viz_type": "heatmap",
        "params": {
            "viz_type": "heatmap",
            "all_columns_x": "grid_col",
            "all_columns_y": "grid_row",
            "metric": _m("xt_value"),
            "adhoc_filters": [],
            "row_limit": 500,
            "normalize_across": "heatmap",
            "left_margin": "auto",
            "bottom_margin": "auto",
            "show_legend": True,
            "show_perc": True,
        },
    },

    # 2 ── Player xT Ranking (horizontal bar)
    {
        "name": "Player xT Ranking",
        "sql": """
SELECT dp.player_name,
       ROUND(SUM(fe.xt_value)::numeric, 2) AS total_xt,
       COUNT(*) AS actions
FROM fact_events fe
JOIN dim_players dp ON fe.player_id = dp.player_id
WHERE fe.xt_value IS NOT NULL
GROUP BY dp.player_name
ORDER BY total_xt DESC
LIMIT 20
""".strip(),
        "viz_type": "echarts_timeseries_bar",
        "params": {
            "viz_type": "echarts_timeseries_bar",
            "x_axis": "player_name",
            "metrics": [_m("total_xt", "Total xT")],
            "groupby": [],
            "adhoc_filters": [],
            "row_limit": 20,
            "x_axis_sort_asc": False,
            "orientation": "horizontal",
            "show_legend": False,
        },
    },

    # 3 ── Team Action Summary (table)
    {
        "name": "Team Action Summary",
        "sql": """
SELECT dt.team_name,
       COUNT(*) AS total_actions,
       ROUND(SUM(fe.xt_value)::numeric, 2) AS total_xt,
       ROUND(AVG(fe.xt_value)::numeric, 4) AS avg_xt,
       COUNT(CASE WHEN fe.event_type = 'Shot' THEN 1 END) AS shots,
       COUNT(CASE WHEN fe.event_type = 'Pass' THEN 1 END) AS passes
FROM fact_events fe
JOIN dim_teams dt ON fe.team_id = dt.team_id
WHERE fe.xt_value IS NOT NULL
GROUP BY dt.team_name
ORDER BY total_xt DESC
""".strip(),
        "viz_type": "table",
        "params": {
            "viz_type": "table",
            "all_columns": ["team_name", "total_actions", "total_xt", "avg_xt", "shots", "passes"],
            "metrics": [],
            "percent_metrics": [],
            "timeseries_limit_metric": None,
            "order_desc": True,
            "row_limit": 100,
            "include_time": False,
            "adhoc_filters": [],
        },
    },

    # 4 ── Set Piece Delivery Clusters (scatter)
    {
        "name": "Set Piece Delivery Clusters",
        "sql": """
SELECT event_type, cluster_label,
       ROUND(center_x::numeric, 1) AS x,
       ROUND(center_y::numeric, 1) AS y,
       member_count
FROM set_piece_clusters
ORDER BY event_type, cluster_label
""".strip(),
        "viz_type": "echarts_scatter",
        "params": {
            "viz_type": "echarts_scatter",
            "x": _m("x", "Pitch X"),
            "y": _m("y", "Pitch Y"),
            "size": _m("member_count", "Cluster Size"),
            "entity": "cluster_label",
            "series": "event_type",
            "adhoc_filters": [],
            "row_limit": 100,
            "show_legend": True,
        },
    },

    # 5 ── Shot Quality by Zone (bar)
    {
        "name": "Shot Quality by Zone",
        "sql": """
SELECT
  CASE
    WHEN location_x > 102 AND location_y BETWEEN 18 AND 62 THEN '6-yard box'
    WHEN location_x > 88  AND location_y BETWEEN 13 AND 67 THEN 'Penalty area'
    WHEN location_x > 60  THEN 'Attacking third'
    ELSE 'Own half'
  END AS zone,
  COUNT(*) AS shots,
  ROUND(AVG(xp_value)::numeric, 4) AS avg_xp
FROM fact_events
WHERE event_type = 'Shot' AND xp_value IS NOT NULL
GROUP BY zone
ORDER BY avg_xp DESC
""".strip(),
        "viz_type": "echarts_timeseries_bar",
        "params": {
            "viz_type": "echarts_timeseries_bar",
            "x_axis": "zone",
            "metrics": [_m("avg_xp", "Avg xP")],
            "groupby": [],
            "adhoc_filters": [],
            "row_limit": 10,
            "x_axis_sort_asc": False,
            "show_legend": False,
        },
    },

    # 6 ── Match xT Balance — home vs away (grouped bar)
    {
        "name": "Match xT Balance",
        "sql": """
SELECT fe.match_id,
       ROUND(SUM(CASE WHEN dt.team_name = dm.home_team THEN fe.xt_value ELSE 0 END)::numeric, 2) AS home_xt,
       ROUND(SUM(CASE WHEN dt.team_name = dm.away_team THEN fe.xt_value ELSE 0 END)::numeric, 2) AS away_xt
FROM fact_events fe
JOIN dim_matches dm ON fe.match_id = dm.match_id
JOIN dim_teams   dt ON fe.team_id  = dt.team_id
WHERE fe.xt_value IS NOT NULL
GROUP BY fe.match_id
ORDER BY fe.match_id
LIMIT 50
""".strip(),
        "viz_type": "echarts_timeseries_bar",
        "params": {
            "viz_type": "echarts_timeseries_bar",
            "x_axis": "match_id",
            "metrics": [_m("home_xt", "Home xT"), _m("away_xt", "Away xT")],
            "groupby": [],
            "adhoc_filters": [],
            "row_limit": 50,
            "show_legend": True,
            "stack": False,
        },
    },

    # 7 ── Press Intensity by Team (horizontal bar)
    {
        "name": "Press Intensity by Team",
        "sql": """
SELECT dt.team_name,
       COUNT(*) AS press_count,
       ROUND(AVG(fe.minute)::numeric, 1) AS avg_minute
FROM fact_events fe
JOIN dim_teams dt ON fe.team_id = dt.team_id
WHERE fe.under_pressure = true
GROUP BY dt.team_name
ORDER BY press_count DESC
LIMIT 20
""".strip(),
        "viz_type": "echarts_timeseries_bar",
        "params": {
            "viz_type": "echarts_timeseries_bar",
            "x_axis": "team_name",
            "metrics": [_m("press_count", "Press Count")],
            "groupby": [],
            "adhoc_filters": [],
            "row_limit": 20,
            "x_axis_sort_asc": False,
            "orientation": "horizontal",
            "show_legend": False,
        },
    },
]


# ── Superset REST client ───────────────────────────────────────────────────────
class SupersetClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base    = base_url.rstrip("/")
        self.session = requests.Session()
        self._login(username, password)

    def _login(self, username: str, password: str) -> None:
        r = self.session.post(
            f"{self.base}/api/v1/security/login",
            json={"username": username, "password": password,
                  "provider": "db", "refresh": True},
        )
        r.raise_for_status()
        self.session.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        r2 = self.session.get(f"{self.base}/api/v1/security/csrf_token/")
        r2.raise_for_status()
        self.session.headers["X-CSRFToken"] = r2.json()["result"]
        self.session.headers["Referer"]     = self.base
        log.info("Superset login OK")

    def get(self, path: str, **kwargs: Any) -> dict:
        r = self.session.get(f"{self.base}{path}", **kwargs)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, payload: dict) -> dict:
        r = self.session.post(f"{self.base}{path}", json=payload)
        r.raise_for_status()
        return r.json()

    def put(self, path: str, payload: dict) -> dict:
        r = self.session.put(f"{self.base}{path}", json=payload)
        r.raise_for_status()
        return r.json()

    def list_all(self, path: str) -> list[dict]:
        """Paginate through a Superset list endpoint and return all items."""
        items: list[dict] = []
        page = 0
        page_size = 100
        while True:
            data = self.get(path, params={"q": json.dumps({"page": page, "page_size": page_size})})
            batch = data.get("result", [])
            items.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        return items


# ── Helpers ────────────────────────────────────────────────────────────────────
def wait_for_superset(max_wait: int = 300) -> None:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            r = requests.get(f"{SUPERSET_URL}/health", timeout=5)
            if r.status_code == 200:
                log.info("Superset is up")
                return
        except requests.exceptions.ConnectionError:
            pass
        log.info("Waiting for Superset…")
        time.sleep(5)
    raise TimeoutError("Superset did not become ready within %d s" % max_wait)


def get_or_create_database(client: SupersetClient) -> int:
    """Return id of the football_analytics database connection."""
    try:
        result = client.post("/api/v1/database/", {
            "database_name":    DB_NAME,
            "sqlalchemy_uri":   DB_CONN_STR,
            "expose_in_sqllab": True,
            "allow_run_async":  False,
            "allow_ctas":       False,
            "allow_cvas":       False,
            "allow_dml":        False,
        })
        db_id = result["id"]
        log.info("Database connection created: id=%s", db_id)
        return db_id
    except requests.HTTPError as exc:
        if exc.response.status_code not in (422, 409):
            raise
        existing = client.list_all("/api/v1/database/")
        matches  = [d for d in existing if d["database_name"] == DB_NAME]
        if not matches:
            raise RuntimeError("Database not found after 422") from exc
        db_id = matches[0]["id"]
        log.info("Reusing database connection: id=%s", db_id)
        return db_id


def get_or_create_dataset(client: SupersetClient, db_id: int,
                           name: str, sql: str) -> int:
    """Create a virtual SQL dataset; return its id (reuse if already exists)."""
    existing = client.list_all("/api/v1/dataset/")
    for ds in existing:
        if ds.get("table_name") == name:
            log.info("Dataset already exists: %s (id=%s)", name, ds["id"])
            return ds["id"]

    result = client.post("/api/v1/dataset/", {
        "database": db_id,
        "table_name": name,
        "sql": sql,
        "schema": "public",
        "is_managed_externally": False,
    })
    ds_id = result["id"]
    log.info("Dataset created: %s (id=%s)", name, ds_id)
    return ds_id


def get_or_create_chart(client: SupersetClient, ds_id: int,
                         chart_def: dict[str, Any]) -> int:
    """Create a chart; return its id (reuse if already exists)."""
    existing = client.list_all("/api/v1/chart/")
    for ch in existing:
        if ch.get("slice_name") == chart_def["name"]:
            log.info("Chart already exists: %s (id=%s)", chart_def["name"], ch["id"])
            return ch["id"]

    params = dict(chart_def["params"])
    params["viz_type"] = chart_def["viz_type"]

    result = client.post("/api/v1/chart/", {
        "datasource_id":   ds_id,
        "datasource_type": "table",
        "viz_type":        chart_def["viz_type"],
        "params":          json.dumps(params),
        "slice_name":      chart_def["name"],
        "owners":          [1],
    })
    chart_id = result["id"]
    log.info("Chart created: %s (id=%s)", chart_def["name"], chart_id)
    return chart_id


def build_position_data(chart_ids: list[int]) -> dict:
    """
    Generate dashboard position_data for a simple 2-column layout.
    Chart 1 (heatmap) gets its own full-width row; the rest pair up.
    """
    pos: dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {
            "type": "ROOT", "id": "ROOT_ID",
            "children": ["GRID_ID"],
        },
        "GRID_ID": {
            "type": "GRID", "id": "GRID_ID",
            "children": [], "parents": ["ROOT_ID"],
        },
    }

    def add_row(row_id: str, parent_chain: list[str],
                charts: list[int], width: int = 6) -> None:
        pos["GRID_ID"]["children"].append(row_id)
        children_ids: list[str] = []
        for cid in charts:
            slot = f"CHART-{cid}"
            children_ids.append(slot)
            pos[slot] = {
                "type": "CHART", "id": slot, "children": [],
                "parents": parent_chain + [row_id],
                "meta": {"chartId": cid, "width": width, "height": 50},
            }
        pos[row_id] = {
            "type": "ROW", "id": row_id,
            "children": children_ids,
            "parents": parent_chain,
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }

    chain = ["ROOT_ID", "GRID_ID"]
    # Row 1: first chart full-width (heatmap)
    add_row("ROW-1", chain, [chart_ids[0]], width=12)
    # Remaining charts in 2-col pairs
    rest = chart_ids[1:]
    for i in range(0, len(rest), 2):
        row_num = i // 2 + 2
        add_row(f"ROW-{row_num}", chain, rest[i : i + 2], width=6)

    return pos


def get_or_create_dashboard(client: SupersetClient,
                              chart_ids: list[int]) -> int:
    """Create the Matchday Analytics dashboard; return its id."""
    existing = client.list_all("/api/v1/dashboard/")
    for db in existing:
        if db.get("dashboard_title") == DASHBOARD_TITLE:
            dash_id = db["id"]
            log.info("Dashboard already exists: id=%s — updating charts", dash_id)
            client.put(f"/api/v1/dashboard/{dash_id}", {
                "position_data": json.dumps(build_position_data(chart_ids)),
                "json_metadata":  json.dumps({"refresh_frequency": 0, "color_scheme": ""}),
            })
            return dash_id

    result = client.post("/api/v1/dashboard/", {
        "dashboard_title": DASHBOARD_TITLE,
        "published":        True,
        "position_data":    json.dumps(build_position_data(chart_ids)),
        "json_metadata":    json.dumps({"refresh_frequency": 0, "color_scheme": ""}),
        "owners":           [1],
    })
    dash_id = result["id"]
    log.info("Dashboard created: %s (id=%s)", DASHBOARD_TITLE, dash_id)
    return dash_id


def publish_dashboard(client: SupersetClient, dash_id: int) -> None:
    client.put(f"/api/v1/dashboard/{dash_id}", {"published": True})
    log.info("Dashboard published: id=%s", dash_id)


# ── Entry point ────────────────────────────────────────────────────────────────
def run() -> None:
    wait_for_superset()
    client = SupersetClient(SUPERSET_URL, ADMIN_USER, ADMIN_PASS)

    db_id = get_or_create_database(client)

    chart_ids: list[int] = []
    for cdef in CHART_DEFS:
        ds_name = f"ds_{cdef['name'].lower().replace(' ', '_').replace('—', '').replace('-', '_')}"
        ds_id   = get_or_create_dataset(client, db_id, ds_name, cdef["sql"])
        ch_id   = get_or_create_chart(client, ds_id, cdef)
        chart_ids.append(ch_id)

    dash_id = get_or_create_dashboard(client, chart_ids)
    publish_dashboard(client, dash_id)

    log.info("=" * 60)
    log.info("xForge Superset setup complete!")
    log.info("  Database : %s", DB_NAME)
    log.info("  Charts   : %d created", len(chart_ids))
    log.info("  Dashboard: %s (id=%s) — Published", DASHBOARD_TITLE, dash_id)
    log.info("=" * 60)


if __name__ == "__main__":
    run()
