"""SQLite connection and session management. Importing this module does not create a DB file."""

import os
import sqlite3
import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("COMPLAINT_DB_PATH", PROJECT_ROOT / "data" / "complaints.sqlite3"))


class Base(DeclarativeBase):
    pass


def make_engine(path: Path) -> Engine:
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


engine = make_engine(DB_PATH)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def initialize_database(db_engine: Engine = engine) -> None:
    import app.db_models  # noqa: F401 - register all mappings
    if db_engine is engine:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Additive migration only. Back up a pre-existing database before any missing table.
        if DB_PATH.exists() and set(Base.metadata.tables) - set(inspect(db_engine).get_table_names()):
            backup = DB_PATH.with_name(DB_PATH.name + ".bak-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
            if backup.exists():
                raise RuntimeError("Refusing to overwrite an existing database backup")
            with sqlite3.connect(DB_PATH) as source, sqlite3.connect(backup) as target:
                source.backup(target)
    Base.metadata.create_all(bind=db_engine)
    # Initialize once, atomically, before concurrent citizen submissions can race.
    with db_engine.begin() as connection:
        connection.execute(text("INSERT OR IGNORE INTO app_secrets (name, value) VALUES (:name, :value)"),
                           {"name": "tracking_hmac_v1", "value": secrets.token_hex(48)})


def get_session():
    with SessionLocal() as session:
        yield session
