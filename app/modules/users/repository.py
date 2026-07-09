from sqlalchemy.orm import Session

from app.db.repository import BaseRepository
from app.modules.users.models import Customer, StaffUser


class CustomerRepository(BaseRepository[Customer]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Customer)

    def get_by_identifier(self, identifier: str) -> Customer | None:
        return (
            self.db.query(Customer)
            .filter((Customer.email == identifier) | (Customer.phone_number == identifier))
            .first()
        )


class StaffUserRepository(BaseRepository[StaffUser]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, StaffUser)

    def get_by_identifier(self, identifier: str) -> StaffUser | None:
        return (
            self.db.query(StaffUser)
            .filter((StaffUser.email == identifier) | (StaffUser.phone_number == identifier))
            .first()
        )
