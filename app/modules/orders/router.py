import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_db_session, require_roles
from app.db.enums import StaffRole
from app.modules.orders.schemas import OrderCreate, OrderRead, OrderStatusTransition
from app.modules.orders.service import OrderService
from app.utils.responses import success_envelope

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, db: Session = Depends(get_db_session)):
    order = OrderService(db).create_order(payload)
    return success_envelope(OrderRead.model_validate(order), "Order placed successfully.")


@router.get("/{order_uuid}")
def get_order(order_uuid: uuid.UUID, db: Session = Depends(get_db_session)):
    order = OrderService(db).get_order(order_uuid)
    return success_envelope(OrderRead.model_validate(order), "Order retrieved successfully.")


@router.patch("/{order_uuid}/status")
def update_order_status(
    order_uuid: uuid.UUID,
    payload: OrderStatusTransition,
    principal=Depends(
        require_roles(StaffRole.SALES_ATTENDANT.value, StaffRole.INVENTORY_MANAGER.value)
    ),
    db: Session = Depends(get_db_session),
):
    order = OrderService(db).transition_status(order_uuid, payload, uuid.UUID(principal.id))
    return success_envelope(OrderRead.model_validate(order), "Order status updated successfully.")


# TODO: GET /orders (list/filter), PATCH /{id} (edit before packed) per API Specification §5.8.
