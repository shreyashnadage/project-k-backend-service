"""
Database connection and session management.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Database URL from environment — defaults to local SQLite for dev
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///tally_sync_dev.db"
)

_is_sqlite = DATABASE_URL.startswith("sqlite")

_engine_kwargs = {
    "echo": os.getenv("SQL_ECHO", "false").lower() == "true",
}
if not _is_sqlite:
    _engine_kwargs.update(pool_size=10, max_overflow=20)
else:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

# Create engine
engine = create_engine(DATABASE_URL, **_engine_kwargs)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """Get database session for dependency injection."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database (create all tables)."""
    from cloudplatform.db.models import Base
    Base.metadata.create_all(bind=engine)
