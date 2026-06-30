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

# Tally Data Simulator URL — empty means test mode is disabled
SIMULATOR_URL = os.getenv("SIMULATOR_URL", "").rstrip("/")

_is_sqlite = DATABASE_URL.startswith("sqlite")

_engine_kwargs = {
    "echo": os.getenv("SQL_ECHO", "false").lower() == "true",
}
if not _is_sqlite:
    # pool_size/max_overflow sized to support ~50 concurrent long-poll connections (55s hold each)
    _engine_kwargs.update(pool_size=30, max_overflow=60, pool_pre_ping=True)
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
    """Initialize database (create all tables + apply lightweight migrations)."""
    from cloudplatform.db.models import Base
    Base.metadata.create_all(bind=engine)
    _apply_migrations()


def _apply_migrations():
    """
    Idempotent column-level migrations for tables that predate create_all.

    PostgreSQL supports ADD COLUMN IF NOT EXISTS (9.6+).
    SQLite swallows the duplicate-column error.
    """
    import logging
    from sqlalchemy import text

    log = logging.getLogger(__name__)
    tables_needing_data_source = [
        "ledgers", "vouchers", "account_groups",
        "stock_items", "stock_groups", "sync_audit_log",
    ]

    with engine.connect() as conn:
        for tbl in tables_needing_data_source:
            if _is_sqlite:
                try:
                    conn.execute(text(
                        f"ALTER TABLE {tbl} ADD COLUMN data_source VARCHAR(50) NOT NULL DEFAULT 'live'"
                    ))
                    conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS ix_{tbl}_data_source ON {tbl} (data_source)"
                    ))
                    log.info(f"Migration: added data_source to {tbl}")
                except Exception:
                    pass  # Column already exists — safe to ignore
            else:
                # PostgreSQL: ADD COLUMN IF NOT EXISTS (no-op if already present)
                try:
                    conn.execute(text(
                        f"ALTER TABLE {tbl} "
                        f"ADD COLUMN IF NOT EXISTS data_source VARCHAR(50) NOT NULL DEFAULT 'live'"
                    ))
                    conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS ix_{tbl}_data_source ON {tbl} (data_source)"
                    ))
                    log.info(f"Migration: ensured data_source on {tbl}")
                except Exception as e:
                    log.warning(f"Migration step skipped for {tbl}: {e}")

        if not _is_sqlite:
            conn.commit()
