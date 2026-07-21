# Self-service profile management for registered customers, plus the
# staff-facing customer directory. Separate from routers/auth.py on
# purpose - auth.py is only the entry points into HAVING an identity
# (register/login), this is what you do once you already have one.

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Customer, CustomerStatus, Order, StaffRole, StaffUser
from routers.orders import _build_order_read
from schemas import CustomerPasswordChange, CustomerProfileUpdate, CustomerRead, OrderRead
from security import get_current_customer, hash_password, require_staff_role, verify_password

router = APIRouter(prefix="/customers", tags=["customers"], route_class=EnvelopeRoute)


@router.get("/me", response_model=CustomerRead)
def read_my_profile(current_customer: Customer = Depends(get_current_customer)):
    return current_customer


@router.patch("/me", response_model=CustomerRead)
def update_my_profile(
    update: CustomerProfileUpdate,
    current_customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    # Only overwrite a field if the client actually sent one - None means
    # "leave this alone", not "clear it", since both fields are optional.
    if update.full_name is not None:
        current_customer.full_name = update.full_name
    if update.phone_number is not None:
        current_customer.phone_number = update.phone_number
    db.commit()
    db.refresh(current_customer)
    return current_customer


@router.patch("/me/password")
def change_my_password(
    change: CustomerPasswordChange,
    current_customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    if not verify_password(change.current_password, current_customer.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")

    current_customer.password_hash = hash_password(change.new_password)
    db.commit()
    return {"message": "Password changed successfully"}


@router.get("/me/orders", response_model=list[OrderRead])
def read_my_orders(
    skip: int = 0,
    limit: int = 10,
    current_customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    orders = (
        db.query(Order)
        .filter(Order.customer_id == current_customer.id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_build_order_read(o, db) for o in orders]


@router.get("", response_model=list[CustomerRead])
def list_customers(
    skip: int = 0,
    limit: int = 10,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    return db.query(Customer).offset(skip).limit(limit).all()


@router.get("/{customer_id}", response_model=CustomerRead)
def read_customer(
    customer_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.patch("/{customer_id}/status", response_model=CustomerRead)
def toggle_customer_status(
    customer_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    # Toggles rather than takes a body - same reasoning as
    # routers/staff.py's toggle_staff_status: the docs describe this as a
    # single "deactivate/reactivate" flip, not a value the caller sets.
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    customer.status = (
        CustomerStatus.INACTIVE if customer.status == CustomerStatus.ACTIVE else CustomerStatus.ACTIVE
    )
    db.commit()
    db.refresh(customer)
    return customer
