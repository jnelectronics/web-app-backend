# Self-service profile management for registered customers, plus the
# staff-facing customer directory. Separate from routers/auth.py on
# purpose - auth.py is only the entry points into HAVING an identity
# (register/login), this is what you do once you already have one.

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Customer, CustomerAddress, CustomerStatus, Order, OwnerType, RefreshToken, StaffRole, StaffUser
from pagination import build_pagination_meta
from routers.orders import _build_order_read
from schemas import (
    CustomerAddressCreate,
    CustomerAddressRead,
    CustomerAddressUpdate,
    CustomerPasswordChange,
    CustomerProfileUpdate,
    CustomerRead,
    CustomerStatusUpdate,
    OrderRead,
    PaginatedResponse,
)
from security import get_current_customer, hash_password, require_staff_role, verify_password

router = APIRouter(prefix="/customers", tags=["customers"], route_class=EnvelopeRoute)

# Frontend's Address Book previously used this same cap - see the
# 2026-08-16 handoff doc. Enforced here (application logic), not in the
# DB - a per-customer COUNT limit isn't something a column constraint can
# express.
MAX_SAVED_ADDRESSES = 3


def _unset_other_default_addresses(db: Session, customer_id: uuid.UUID, exclude_id: uuid.UUID | None = None) -> None:
    # "At most one is_default per customer" - see CustomerAddress
    # .is_default's own comment in models.py for why this lives here
    # instead of a DB constraint. Called right before a write that's about
    # to make ONE address the default, so every other one for the same
    # customer stops being the default first.
    query = db.query(CustomerAddress).filter(
        CustomerAddress.customer_id == customer_id, CustomerAddress.is_default.is_(True)
    )
    if exclude_id is not None:
        query = query.filter(CustomerAddress.id != exclude_id)
    query.update({CustomerAddress.is_default: False}, synchronize_session=False)


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


@router.patch("/me/status", response_model=CustomerRead)
def deactivate_my_account(
    update: CustomerStatusUpdate,
    current_customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    # Self-service account closure (frontend handoff doc, 2026-08-18,
    # "Option A" of the two the doc offered). Reuses CustomerStatusUpdate
    # (the same schema the staff-only PATCH /{customer_id}/status below
    # takes) but a customer acting on THEIR OWN account through this route
    # can only ever move to inactive, never back to active - reactivating
    # a closed account stays a staff-only action via that other endpoint.
    if update.status != CustomerStatus.INACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only deactivate your own account through this endpoint",
        )

    if current_customer.status == CustomerStatus.INACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account is already inactive")

    current_customer.status = CustomerStatus.INACTIVE

    # Same "log out everywhere" revocation routers/auth.py's /auth/logout
    # does - a customer who just closed their account shouldn't be able to
    # keep browsing as signed in on an access token minted from a refresh
    # token that predates the closure.
    now = datetime.now(timezone.utc)
    active_tokens = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.owner_type == OwnerType.CUSTOMER,
            RefreshToken.owner_id == current_customer.id,
            RefreshToken.revoked_at.is_(None),
        )
        .all()
    )
    for token_row in active_tokens:
        token_row.revoked_at = now

    db.commit()
    db.refresh(current_customer)
    return current_customer


@router.get("/me/addresses", response_model=list[CustomerAddressRead])
def list_my_addresses(
    current_customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    # Default address first (if any), then oldest-first among the rest -
    # a stable, predictable order for the Address Book UI to render.
    return (
        db.query(CustomerAddress)
        .filter(CustomerAddress.customer_id == current_customer.id)
        .order_by(CustomerAddress.is_default.desc(), CustomerAddress.created_at)
        .all()
    )


@router.post("/me/addresses", response_model=CustomerAddressRead)
def create_my_address(
    address: CustomerAddressCreate,
    current_customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    existing_count = (
        db.query(CustomerAddress).filter(CustomerAddress.customer_id == current_customer.id).count()
    )
    if existing_count >= MAX_SAVED_ADDRESSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You can save at most {MAX_SAVED_ADDRESSES} addresses",
        )

    if address.is_default:
        _unset_other_default_addresses(db, current_customer.id)

    new_address = CustomerAddress(
        customer_id=current_customer.id,
        label=address.label,
        recipient_name=address.recipient_name,
        phone_number=address.phone_number,
        address_line=address.address_line,
        is_default=address.is_default,
    )
    db.add(new_address)
    db.commit()
    db.refresh(new_address)
    return new_address


@router.patch("/me/addresses/{address_id}", response_model=CustomerAddressRead)
def update_my_address(
    address_id: uuid.UUID,
    update: CustomerAddressUpdate,
    current_customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    existing = (
        db.query(CustomerAddress)
        .filter(CustomerAddress.id == address_id, CustomerAddress.customer_id == current_customer.id)
        .first()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Address not found")

    if update.label is not None:
        existing.label = update.label
    if update.recipient_name is not None:
        existing.recipient_name = update.recipient_name
    if update.phone_number is not None:
        existing.phone_number = update.phone_number
    if update.address_line is not None:
        existing.address_line = update.address_line
    if update.is_default is not None:
        if update.is_default:
            _unset_other_default_addresses(db, current_customer.id, exclude_id=existing.id)
        existing.is_default = update.is_default

    db.commit()
    db.refresh(existing)
    return existing


@router.delete("/me/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_address(
    address_id: uuid.UUID,
    current_customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    # A real delete, not a soft one - unlike products/categories/etc, the
    # docs describe this as "Remove a saved address", and there's no
    # audit/history need for a customer's own address book the way there
    # is for catalog data staff manage.
    existing = (
        db.query(CustomerAddress)
        .filter(CustomerAddress.id == address_id, CustomerAddress.customer_id == current_customer.id)
        .first()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Address not found")

    db.delete(existing)
    db.commit()


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


@router.get("", response_model=PaginatedResponse[CustomerRead])
def list_customers(
    skip: int = 0,
    limit: int = 10,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    total = db.query(Customer).count()
    customers = db.query(Customer).offset(skip).limit(limit).all()
    return PaginatedResponse[CustomerRead](
        items=customers, pagination=build_pagination_meta(skip, limit, total)
    )


@router.get("/{customer_id}", response_model=CustomerRead)
def read_customer(
    customer_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.patch("/{customer_id}/status", response_model=CustomerRead)
def set_customer_status(
    customer_id: uuid.UUID,
    update: CustomerStatusUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    # Sets status to whatever the caller asked for, rather than blindly
    # flipping it - see routers/staff.py's set_staff_status for the full
    # idempotency reasoning (a toggle isn't safe to retry/double-click).
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    customer.status = update.status
    db.commit()
    db.refresh(customer)
    return customer
