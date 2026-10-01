"""SQLAlchemy session boundary. Routes/services explicitly commit their transactions."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={"options": "-c timezone=UTC"},
)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)


def get_session() -> Generator[Session, None, None]:
    """Yield a request-local session, rolling back any uncommitted work on close."""
    with SessionLocal() as session:
        yield session
