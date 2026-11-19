"""PostgreSQL configuration and request-scoped sessions; no connection at import."""

from functools import lru_cache
import os

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import Session


def database_url(environ=None):
    env = os.environ if environ is None else environ
    required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    if any(not env.get(name) for name in required):
        raise ValueError("Configure DB_HOST, DB_NAME, DB_USER et DB_PASSWORD.")
    try:
        port = int(env.get("DB_PORT", "5432"))
    except ValueError:
        raise ValueError("DB_PORT doit être un entier entre 1 et 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("DB_PORT doit être un entier entre 1 et 65535.")
    return URL.create(
        "postgresql+psycopg", host=env["DB_HOST"], port=port,
        database=env["DB_NAME"], username=env["DB_USER"], password=env["DB_PASSWORD"],
    )


@lru_cache(maxsize=1)
def get_engine():
    return create_engine(
        database_url(), pool_pre_ping=True, hide_parameters=True,
        connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"},
    )


def get_session():
    """FastAPI dependency. Callers explicitly commit their write transactions."""
    with Session(get_engine(), autoflush=False, expire_on_commit=False) as session:
        yield session
