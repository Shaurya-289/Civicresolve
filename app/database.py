"""SQLite locally, or PostgreSQL when DATABASE_URL is configured."""

import os
import sqlite3
import secrets
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)
DB_PATH = Path(os.environ.get("COMPLAINT_DB_PATH", PROJECT_ROOT / "data" / "complaints.sqlite3"))
DATABASE_URL = os.environ.get("DATABASE_URL")


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


def make_postgres_engine(database_url: str) -> Engine:
    """Use Neon over TLS without keeping idle connections in Vercel workers."""
    if database_url.startswith("postgres://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
    elif database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]
    elif not database_url.startswith("postgresql+psycopg://"):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
    return create_engine(
        database_url,
        poolclass=NullPool,
        connect_args={"connect_timeout": 10, "sslmode": "require"},
    )


engine = make_postgres_engine(DATABASE_URL) if DATABASE_URL else make_engine(DB_PATH)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def initialize_database(db_engine: Engine = engine) -> None:
    import app.db_models  # noqa: F401 - register all mappings
    if db_engine is engine and db_engine.dialect.name == "sqlite":
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
        connection.execute(text("INSERT INTO app_secrets (name, value) VALUES (:name, :value) "
                                "ON CONFLICT(name) DO NOTHING"),
                           {"name": "tracking_hmac_v1", "value": secrets.token_hex(48)})


def verify_database_ready(db_engine: Engine = engine) -> None:
    """Vercel workers must never create or migrate a production schema."""
    import app.db_models  # noqa: F401 - register all mappings
    with db_engine.connect() as connection:
        missing = set(Base.metadata.tables) - set(inspect(connection).get_table_names())
        if missing:
            raise RuntimeError("PostgreSQL schema is not initialized; run the one-time bootstrap")
        if connection.scalar(text("SELECT value FROM app_secrets WHERE name = 'tracking_hmac_v1'")) is None:
            raise RuntimeError("PostgreSQL application secret is missing; run the one-time bootstrap")


def begin_serialized_write(session: Session) -> None:
    """Serialize read-modify-write admin operations on both supported databases."""
    session.commit()
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    elif dialect == "postgresql":
        from app.db_models import AppSecret
        secret = session.scalar(select(AppSecret.name).where(AppSecret.name == "tracking_hmac_v1").with_for_update())
        if secret is None:
            raise RuntimeError("PostgreSQL application secret is missing")
    else:
        raise RuntimeError("Unsupported database backend")


def get_session():
    with SessionLocal() as session:
        yield session
