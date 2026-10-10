"""One-time Neon schema and first-admin setup, run from a trusted local terminal."""

import argparse
import getpass
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize a new PostgreSQL database for CivicResolve")
    parser.add_argument("--admin-only", action="store_true", help="create the first admin after schema setup")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parent.parent / ".env.vercel.local", override=False)

    from sqlalchemy import inspect
    from app.database import SessionLocal, engine, initialize_database, verify_database_ready
    from app.security import initialize_admin

    if engine.dialect.name != "postgresql":
        sys.exit("DATABASE_URL must point to PostgreSQL; no database was changed")
    if args.admin_only:
        verify_database_ready()
    elif inspect(engine).get_table_names():
        sys.exit("Database already contains tables; bootstrap refused to change it")

    username = input("First administrator ID: ").strip()
    password = getpass.getpass("Password (at least 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation or len(password) < 12:
        sys.exit("Passwords must match and contain at least 12 characters")

    if not args.admin_only:
        initialize_database()
    with SessionLocal() as session:
        try:
            initialize_admin(session, username, password)
        except ValueError as exc:
            sys.exit(str(exc))
    print("PostgreSQL schema and administrator are ready.")


if __name__ == "__main__":
    main()
