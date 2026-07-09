"""One-off script to seed the initial System Administrator account (FR-AUTH-011).

Run via: docker compose exec api python -m scripts.seed_admin
Not exposed via the API since no account yet has permission to create it.
"""

import getpass

from app.core.security import hash_password
from app.db.enums import StaffRole
from app.db.session import SessionLocal
from app.modules.users.models import StaffUser
from app.modules.users.repository import StaffUserRepository


def main() -> None:
    email = input("System Administrator email: ").strip()
    full_name = input("Full name: ").strip()
    password = getpass.getpass("Password: ")

    db = SessionLocal()
    try:
        repository = StaffUserRepository(db)
        if repository.get_by_identifier(email):
            print(f"A staff account with email {email} already exists. Aborting.")
            return

        repository.create(
            StaffUser(
                full_name=full_name,
                email=email,
                password_hash=hash_password(password),
                role=StaffRole.SYSTEM_ADMINISTRATOR,
            )
        )
        print(f"System Administrator '{email}' created successfully.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
