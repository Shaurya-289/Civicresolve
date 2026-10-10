"""Create the first or an additional administrator without a default password."""

import getpass
import sys

from app.database import SessionLocal, initialize_database
from app.security import initialize_admin


def main():
    username = input("Administrator ID: ").strip()
    password = getpass.getpass("Password (at least 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        sys.exit("Passwords did not match")
    initialize_database()
    with SessionLocal() as session:
        try:
            row = initialize_admin(session, username, password)
        except ValueError as exc:
            sys.exit(str(exc))
    print(f"Administrator {row.username} created")


if __name__ == "__main__":
    main()
