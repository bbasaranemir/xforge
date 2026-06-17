import os

from sqlalchemy import create_engine


def get_engine():
    user   = os.environ.get("POSTGRES_USER",    "analytics")
    passwd = os.environ.get("POSTGRES_PASSWORD", "analytics")
    host   = os.environ.get("POSTGRES_HOST",     "postgres")
    db     = os.environ.get("POSTGRES_DB",       "football_db")
    return create_engine(
        f"postgresql+psycopg2://{user}:{passwd}@{host}:5432/{db}",
        pool_pre_ping=True,
    )
