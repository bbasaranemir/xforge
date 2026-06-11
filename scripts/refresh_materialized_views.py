import logging
import os

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

_user = os.environ.get("POSTGRES_USER", "analytics")
_pass = os.environ.get("POSTGRES_PASSWORD", "analytics")
_host = os.environ.get("POSTGRES_HOST", "postgres")
_db = os.environ.get("POSTGRES_DB", "football_db")
DB_URL = f"postgresql+psycopg2://{_user}:{_pass}@{_host}:5432/{_db}"

# Whitelist prevents any f-string SQL injection risk.
# CONCURRENTLY = zero read-lock; requires unique indexes from 02_v2_migration.sql.
_ALLOWED_MVS = {"mv_team_xg", "mv_shot_locations"}
MATERIALIZED_VIEWS = ["mv_team_xg", "mv_shot_locations"]


def run():
    engine = create_engine(DB_URL, pool_pre_ping=True)
    with engine.begin() as conn:
        for mv in MATERIALIZED_VIEWS:
            if mv not in _ALLOWED_MVS:
                raise ValueError(f"Unknown materialized view: {mv}")
            conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}"))
            log.info("Refreshed: %s", mv)
    log.info("All materialized views refreshed.")


if __name__ == "__main__":
    run()
