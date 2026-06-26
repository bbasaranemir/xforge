import os

from sqlalchemy import create_engine


def get_engine():
    user   = os.environ["POSTGRES_USER"]
    passwd = os.environ["POSTGRES_PASSWORD"]
    host   = os.environ.get("POSTGRES_HOST", "postgres")
    db     = os.environ["POSTGRES_DB"]
    return create_engine(
        f"postgresql+psycopg2://{user}:{passwd}@{host}:5432/{db}",
        pool_pre_ping=True,
    )
