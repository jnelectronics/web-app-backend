import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_db_session, require_roles
from app.db.enums import StaffRole
from app.modules.users.schemas import (
    CustomerPasswordChange,
    CustomerRead,
    CustomerStatusUpdate,
    CustomerUpdate,
)
from app.modules.users.service import CustomerService
from app.utils.responses import paginated_envelope, success_envelope

customers_router = APIRouter(prefix="/customers", tags=["Customers"])
staff_router = APIRouter(prefix="/staff", tags=["Staff Users"])


@customers_router.get("/me")
def get_my_profile(
    principal=Depends(require_roles("registered_customer")),
    db: Session = Depends(get_db_session),
):
    customer = CustomerService(db).get_own_profile(uuid.UUID(principal.id))
    return success_envelope(CustomerRead.model_validate(customer), "Profile retrieved successfully.")


@customers_router.patch("/me")
def update_my_profile(
    payload: CustomerUpdate,
    principal=Depends(require_roles("registered_customer")),
    db: Session = Depends(get_db_session),
):
    customer = CustomerService(db).update_own_profile(uuid.UUID(principal.id), payload)
    return success_envelope(CustomerRead.model_validate(customer), "Profile updated successfully.")


@customers_router.patch("/me/password")
def change_my_password(
    payload: CustomerPasswordChange,
    principal=Depends(require_roles("registered_customer")),
    db: Session = Depends(get_db_session),
):
    CustomerService(db).change_own_password(uuid.UUID(principal.id), payload)
    return success_envelope(None, "Password changed successfully.")


@customers_router.get("")
def list_customers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    principal=Depends(require_roles(StaffRole.INVENTORY_MANAGER.value)),
    db: Session = Depends(get_db_session),
):
    customers, total = CustomerService(db).list_customers(page=page, page_size=page_size)
    return paginated_envelope(
        [CustomerRead.model_validate(c) for c in customers],
        page=page,
        page_size=page_size,
        total_records=total,
        message="Customers retrieved successfully.",
    )


@customers_router.get("/{customer_uuid}")
def get_customer(
    customer_uuid: uuid.UUID,
    principal=Depends(require_roles(StaffRole.INVENTORY_MANAGER.value)),
    db: Session = Depends(get_db_session),
):
    customer = CustomerService(db).get_customer(customer_uuid)
    return success_envelope(CustomerRead.model_validate(customer), "Customer retrieved successfully.")


@customers_router.patch("/{customer_uuid}/status")
def set_customer_status(
    customer_uuid: uuid.UUID,
    payload: CustomerStatusUpdate,
    principal=Depends(require_roles(StaffRole.INVENTORY_MANAGER.value)),
    db: Session = Depends(get_db_session),
):
    customer = CustomerService(db).set_status(customer_uuid, payload)
    return success_envelope(CustomerRead.model_validate(customer), "Customer status updated successfully.")
