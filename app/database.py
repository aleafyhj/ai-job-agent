"""Small synchronous database boundary; transactions never span external calls."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=10000"},
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
