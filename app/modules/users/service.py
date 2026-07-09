import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.security import hash_password, verify_password
from app.modules.users.models import Customer
from app.modules.users.repository import CustomerRepository
from app.modules.users.schemas import CustomerPasswordChange, CustomerStatusUpdate, CustomerUpdate


class CustomerService:
    def __init__(self, db: Session) -> None:
        self.repository = CustomerRepository(db)

    def get_own_profile(self, customer_id: uuid.UUID) -> Customer:
        customer = self.repository.get(customer_id)
        if not customer:
            raise NotFoundError("Customer not found.")
        return customer

    def update_own_profile(self, customer_id: uuid.UUID, payload: CustomerUpdate) -> Customer:
        customer = self.get_own_profile(customer_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(customer, field, value)
        return self.repository.save(customer)

    def change_own_password(self, customer_id: uuid.UUID, payload: CustomerPasswordChange) -> None:
        customer = self.get_own_profile(customer_id)
        if not customer.password_hash or not verify_password(
            payload.current_password, customer.password_hash
        ):
            raise NotFoundError("Current password is incorrect.")
        customer.password_hash = hash_password(payload.new_password)
        self.repository.save(customer)

    def list_customers(self, *, page: int, page_size: int) -> tuple[list[Customer], int]:
        offset = (page - 1) * page_size
        return self.repository.list(offset=offset, limit=page_size), self.repository.count()

    def get_customer(self, customer_id: uuid.UUID) -> Customer:
        return self.get_own_profile(customer_id)

    def set_status(self, customer_id: uuid.UUID, payload: CustomerStatusUpdate) -> Customer:
        customer = self.get_own_profile(customer_id)
        customer.status = payload.status
        return self.repository.save(customer)
