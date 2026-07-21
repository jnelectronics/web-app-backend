# Run this ONCE, manually, to create the very first System Administrator
# account: `python seed_admin.py`
#
# This is the only way a system_administrator account can ever come into
# existence - routers/staff.py deliberately refuses to create one through
# the API, since letting the API create the account that grants full
# access to the API would make that whole boundary meaningless.

from database import SessionLocal
from models import StaffRole, StaffUser
from security import hash_password

ADMIN_EMAIL = "admin@jnelectronics.com"
ADMIN_PASSWORD = "changeme123"  # noqa: change this immediately after first login


def seed_admin():
    db = SessionLocal()
    try:
        existing = db.query(StaffUser).filter(StaffUser.email == ADMIN_EMAIL).first()
        if existing is not None:
            print(f"Admin account already exists: {ADMIN_EMAIL}")
            return

        admin = StaffUser(
            full_name="System Administrator",
            email=ADMIN_EMAIL,
            password_hash=hash_password(ADMIN_PASSWORD),
            role=StaffRole.SYSTEM_ADMINISTRATOR,
        )
        db.add(admin)
        db.commit()
        print(f"Created System Administrator: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin()
