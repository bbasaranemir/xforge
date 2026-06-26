"""
One-shot Superset bootstrap via REST API.
Creates the football_db connection and registers all saved queries.
Idempotent: safe to re-run; existing connection is reused on 422.
"""

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

SUPERSET_URL = os.environ.get("SUPERSET_URL", "http://superset:8088")
ADMIN_USER = os.environ.get("SUPERSET_USER", "admin")
ADMIN_PASS = os.environ.get("SUPERSET_PASSWORD", "admin")

DB_CONN_STR = (
    f"postgresql+psycopg2://{os.environ.get('POSTGRES_USER', 'analytics')}"
    f":{os.environ.get('POSTGRES_PASSWORD', 'analytics')}"
    f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:5432"
    f"/{os.environ.get('POSTGRES_DB', 'football_db')}"
)

SAVED_QUERIES = [
    {
        "label": "xT Heatmap Grid",
        "description": "16x12 grid — total xT per cell",
        "schema": "public",
        "sql": "SELECT grid_col, grid_row, xt_value, round((xt_value / nullif(max(xt_value) over (), 0))::numeric, 4) AS xt_normalised FROM xt_surface ORDER BY grid_row, grid_col;",  # noqa: E501
    },
    {
        "label": "Player xT Ranking",
        "description": "Top 20 players by total xT across all matches",
        "schema": "analytics_analytics_marts",
        "sql": "SELECT player_name, team_name, total_passes, successful_passes, pass_completion_pct, avg_pass_distance, total_xt, avg_xt_per_pass, passes_under_pressure FROM analytics_analytics_marts.mart_player_metrics WHERE total_xt IS NOT NULL ORDER BY total_xt DESC LIMIT 20;",  # noqa: E501
    },
    {
        "label": "Team Action Summary",
        "description": "Per-team action counts and total xT",
        "schema": "analytics_analytics_marts",
        "sql": "SELECT team_name, total_actions, total_passes, total_shots, total_dribbles, total_pressures, total_xt FROM analytics_analytics_marts.mart_team_summary ORDER BY total_xt DESC NULLS LAST;",  # noqa: E501
    },
    {
        "label": "xT by Pitch Zone",
        "description": "Aggregated xT by attacking / middle / defensive third",
        "schema": "public",
        "sql": "SELECT CASE WHEN grid_col BETWEEN 0 AND 4 THEN 'Defensive Third' WHEN grid_col BETWEEN 5 AND 10 THEN 'Middle Third' WHEN grid_col BETWEEN 11 AND 15 THEN 'Attacking Third' END AS pitch_zone, sum(xt_value) AS total_xt, round(avg(xt_value)::numeric, 4) AS avg_xt, count(*) AS cell_count FROM xt_surface GROUP BY 1 ORDER BY total_xt DESC;",  # noqa: E501
    },
    {
        "label": "Top 25 xT Actions",
        "description": "Highest individual xT events — feeds XML export",
        "schema": "public",
        "sql": "SELECT fe.minute, fe.second, fe.event_type, dp.player_name, dt.team_name, round(fe.location_x::numeric, 1) AS loc_x, round(fe.location_y::numeric, 1) AS loc_y, round(fe.end_location_x::numeric, 1) AS end_x, round(fe.end_location_y::numeric, 1) AS end_y, round(fe.xt_value::numeric, 4) AS xt_value, round(fe.xp_value::numeric, 4) AS xp_value, fe.under_pressure FROM fact_events fe LEFT JOIN dim_players dp ON fe.player_id = dp.player_id LEFT JOIN dim_teams dt ON fe.team_id = dt.team_id WHERE fe.xt_value IS NOT NULL AND fe.event_type IN ('Pass', 'Carry', 'Shot') ORDER BY fe.xt_value DESC LIMIT 25;",  # noqa: E501
    },
    {
        "label": "Set Piece Cluster Centroids",
        "description": "K-Means cluster centres for corner and free-kick deliveries",
        "schema": "public",
        "sql": "SELECT event_type, cluster_label, center_x, center_y, member_count, trained_at FROM set_piece_clusters ORDER BY event_type, cluster_label;",  # noqa: E501
    },
    {
        "label": "xP Model Registry",
        "description": "Trained model performance history",
        "schema": "public",
        "sql": "SELECT model_name, version, trained_at, metrics->>'auc' AS auc, metrics->>'log_loss' AS log_loss, metrics->>'train_size' AS train_size FROM model_registry ORDER BY trained_at DESC;",  # noqa: E501
    },
    {
        "label": "Competition xT Comparison",
        "description": "Average xT per match by competition",
        "schema": "public",
        "sql": "SELECT dc.competition_name, COUNT(DISTINCT fe.match_id) AS total_matches, ROUND(SUM(fe.xt_value)::numeric, 2) AS total_xt, ROUND(AVG(fe.xt_value)::numeric, 5) AS avg_xt_per_event, ROUND((SUM(fe.xt_value) / NULLIF(COUNT(DISTINCT fe.match_id), 0))::numeric, 3) AS avg_xt_per_match FROM fact_events fe JOIN dim_matches dm ON fe.match_id = dm.match_id JOIN dim_competitions dc ON dm.competition_id = dc.competition_id WHERE fe.xt_value IS NOT NULL GROUP BY dc.competition_name ORDER BY avg_xt_per_match DESC;",  # noqa: E501
    },
    {
        "label": "Shot Quality by Zone",
        "description": "xP distribution by pitch zone for shots",
        "schema": "public",
        "sql": "SELECT CASE WHEN location_x >= 102 AND location_y BETWEEN 18 AND 62 THEN 'Six Yard Box' WHEN location_x >= 88 AND location_y BETWEEN 13 AND 67 THEN 'Penalty Area' WHEN location_x >= 80 THEN 'Attacking Third' ELSE 'Long Range' END AS shot_zone, COUNT(*) AS total_shots, ROUND(AVG(xp_value)::numeric, 3) AS avg_xp, ROUND(SUM(xp_value)::numeric, 2) AS total_xp FROM fact_events WHERE event_type = 'Shot' AND xp_value IS NOT NULL AND location_x IS NOT NULL GROUP BY 1 ORDER BY avg_xp DESC;",  # noqa: E501
    },
    {
        "label": "Match xT Balance",
        "description": "xT dominance per match — who controlled the game",
        "schema": "public",
        "sql": "SELECT dm.match_id, dm.match_date, dm.home_team, dm.away_team, dm.home_score, dm.away_score, ROUND(SUM(CASE WHEN dt.team_name = dm.home_team THEN fe.xt_value ELSE 0 END)::numeric, 3) AS home_xt, ROUND(SUM(CASE WHEN dt.team_name = dm.away_team THEN fe.xt_value ELSE 0 END)::numeric, 3) AS away_xt, ROUND((SUM(CASE WHEN dt.team_name = dm.home_team THEN fe.xt_value ELSE 0 END) - SUM(CASE WHEN dt.team_name = dm.away_team THEN fe.xt_value ELSE 0 END))::numeric, 3) AS xt_difference FROM fact_events fe JOIN dim_matches dm ON fe.match_id = dm.match_id JOIN dim_teams dt ON fe.team_id = dt.team_id WHERE fe.xt_value IS NOT NULL GROUP BY dm.match_id, dm.match_date, dm.home_team, dm.away_team, dm.home_score, dm.away_score ORDER BY ABS(xt_difference) DESC LIMIT 50;",  # noqa: E501
    },
    {
        "label": "Press Intensity by Team",
        "description": "Pressing actions count per team",
        "schema": "public",
        "sql": "SELECT dt.team_name, COUNT(*) AS total_press_actions, COUNT(DISTINCT fe.match_id) AS matches_played, ROUND((COUNT(*) / NULLIF(COUNT(DISTINCT fe.match_id), 0))::numeric, 1) AS press_per_match FROM fact_events fe JOIN dim_teams dt ON fe.team_id = dt.team_id WHERE fe.event_type IN ('Pressure', 'Interception', 'Tackle', 'Block') GROUP BY dt.team_name ORDER BY press_per_match DESC LIMIT 20;",  # noqa: E501
    },
]


class SupersetClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self._login(username, password)

    def _login(self, username: str, password: str) -> None:
        r = self.session.post(
            f"{self.base}/api/v1/security/login",
            json={
                "username": username,
                "password": password,
                "provider": "db",
                "refresh": True,
            },
        )
        r.raise_for_status()
        self.session.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        r = self.session.get(f"{self.base}/api/v1/security/csrf_token/")
        r.raise_for_status()
        self.session.headers["X-CSRFToken"] = r.json()["result"]
        self.session.headers["Referer"] = self.base
        log.info("Superset login successful")

    def post(self, path: str, payload: dict) -> dict:
        r = self.session.post(f"{self.base}{path}", json=payload)
        r.raise_for_status()
        return r.json()

    def get(self, path: str) -> dict:
        r = self.session.get(f"{self.base}{path}")
        r.raise_for_status()
        return r.json()


def wait_for_superset(max_wait: int = 180) -> None:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            r = requests.get(f"{SUPERSET_URL}/health", timeout=5)
            if r.status_code == 200:
                log.info("Superset is up")
                return
        except requests.exceptions.ConnectionError:
            pass
        log.info("Waiting for Superset...")
        time.sleep(5)
    raise TimeoutError("Superset did not become ready in time")


def get_or_create_database(client: SupersetClient) -> int:
    payload = {
        "database_name": "football_db",
        "sqlalchemy_uri": DB_CONN_STR,
        "expose_in_sqllab": True,
        "allow_run_async": False,
        "allow_ctas": False,
        "allow_cvas": False,
        "allow_dml": False,
    }
    try:
        result = client.post("/api/v1/database/", payload)
        db_id = result["id"]
        log.info("Database connection created: id=%s", db_id)
        return db_id
    except requests.HTTPError as e:
        if e.response.status_code != 422:
            raise
        existing = client.get("/api/v1/database/")
        matches = [d for d in existing["result"] if d["database_name"] == "football_db"]
        if not matches:
            raise RuntimeError("football_db not found in Superset") from e
        db_id = matches[0]["id"]
        log.info("Reusing existing database connection: id=%s", db_id)
        return db_id


def create_saved_queries(client: SupersetClient, db_id: int) -> None:
    for q in SAVED_QUERIES:
        try:
            client.post(
                "/api/v1/saved_query/",
                {
                    "db_id": db_id,
                    "label": q["label"],
                    "description": q["description"],
                    "sql": q["sql"].strip(),
                    "schema": q["schema"],
                },
            )
            log.info("Saved query: %s", q["label"])
        except requests.HTTPError as e:
            log.warning("Could not create query '%s': %s", q["label"], e)


def run() -> None:
    wait_for_superset()
    client = SupersetClient(SUPERSET_URL, ADMIN_USER, ADMIN_PASS)
    db_id = get_or_create_database(client)
    create_saved_queries(client, db_id)
    log.info("Superset initialisation complete.")


if __name__ == "__main__":
    run()
