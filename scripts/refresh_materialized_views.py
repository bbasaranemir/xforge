import logging

from sqlalchemy import text

from db_utils import get_engine

log = logging.getLogger(__name__)

# Whitelist prevents any f-string SQL injection risk.
# CONCURRENTLY = zero read-lock; requires unique indexes from 02_v2_migration.sql.
_ALLOWED_MVS = {"mv_team_xg", "mv_shot_locations"}
MATERIALIZED_VIEWS = ["mv_team_xg", "mv_shot_locations"]


def run():
    engine = get_engine()
    with engine.begin() as conn:
        for mv in MATERIALIZED_VIEWS:
            if mv not in _ALLOWED_MVS:
                raise ValueError(f"Unknown materialized view: {mv}")
            conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY " + mv))
            log.info("Refreshed: %s", mv)
    log.info("All materialized views refreshed.")


if __name__ == "__main__":
    run()
