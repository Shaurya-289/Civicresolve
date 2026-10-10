"""Fast checks for the Vercel entrypoint and PostgreSQL compatibility."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable

from app.database import Base, make_postgres_engine
from index import app as vercel_app
from app.main import app as local_app
import app.db_models  # noqa: F401 - register mappings


def test_vercel_entrypoint_exports_the_existing_application():
    assert vercel_app is local_app
    assert any(getattr(route, "path", None) == "/classify" for route in vercel_app.routes)
    assert any(getattr(route, "path", None) == "/portal/admin/login" for route in vercel_app.routes)


def test_postgres_url_and_schema_compile_without_a_database_connection():
    engine = make_postgres_engine("postgres://demo:demo@localhost/civicresolve")
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        assert isinstance(engine.pool, NullPool)
        assert {"administrators", "admin_sessions", "resources", "resource_allocations", "budget_policy"} <= set(Base.metadata.tables)
        for table in Base.metadata.sorted_tables:
            assert str(CreateTable(table).compile(dialect=postgresql.dialect()))
    finally:
        engine.dispose()
