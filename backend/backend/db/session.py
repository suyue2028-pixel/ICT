from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.config import get_settings


@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True, hide_parameters=True)


@lru_cache
def get_session_factory():
    return sessionmaker(get_engine(), expire_on_commit=False)


def get_db():
    with get_session_factory()() as session:
        yield session
