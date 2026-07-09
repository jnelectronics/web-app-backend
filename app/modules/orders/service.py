import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.exceptions import InsufficientInventoryError, InvalidStateTransitionError, NotFoundError
from app.db.enums import MovementType
from app.modules.inventory.models import InventoryMovement, InventoryRecord
from app.modules.orders.models import Order, OrderItem, OrderStatusHistory
from app.modules.orders.repository import OrderRepository
from app.modules.orders.schemas import VALID_TRANSITIONS, OrderCreate, OrderStatusTransition
from app.modules.products.repository import ProductVariantRepository


class OrderService:
    """FR-ORDER-001-015: order creation locks inventory and decrements stock in one transaction."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = OrderRepository(db)
        self.variants = ProductVariantRepository(db)

    def get_order(self, order_id: uuid.UUID) -> Order:
        order = self.repository.get(order_id)
        if not order:
            raise NotFoundError("Order not found.")
        return order

    def create_order(self, payload: OrderCreate, customer_id: uuid.UUID | None = None) -> Order:
        order_number = f"JN-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
        subtotal = 0
        order_items: list[OrderItem] = []

        try:
            for item in payload.items:
                variant = self.variants.get(item.variant_id)
                if not variant:
                    raise NotFoundError(f"Variant {item.variant_id} not found.")

                record = (
                    self.db.query(InventoryRecord)
                    .filter(InventoryRecord.variant_id == item.variant_id)
                    .with_for_update()
                    .first()
                )
                if not record or record.quantity_available < item.quantity:
                    raise InsufficientInventoryError(
                        f"Insufficient stock for variant {item.variant_id}."
                    )

                record.quantity_available -= item.quantity
                self.db.add(
                    InventoryMovement(
                        inventory_record_id=record.id,
                        movement_type=MovementType.SOLD,
                        quantity_changed=-item.quantity,
                    )
                )

                line_total = variant.price * item.quantity
                subtotal += line_total
                order_items.append(
                    OrderItem(
                        variant_id=variant.id,
                        product_name_snapshot=variant.product.name,
                        variant_label_snapshot=variant.variant_label,
                        quantity=item.quantity,
                        unit_price=variant.price,
                        line_total=line_total,
                    )
                )

            order = Order(
                order_number=order_number,
                customer_id=customer_id,
                guest_full_name=payload.guest_full_name,
                guest_phone_number=payload.guest_phone_number,
                guest_email=payload.guest_email,
                delivery_address=payload.delivery_address,
                subtotal=subtotal,
                total=subtotal,
                items=order_items,
            )
            self.db.add(order)
            self.db.commit()
            self.db.refresh(order)
            # TODO: enqueue send_order_confirmation_email / notify_staff_new_order (app/workers/).
            return order
        except Exception:
            self.db.rollback()
            raise

    def transition_status(
        self, order_id: uuid.UUID, payload: OrderStatusTransition, staff_user_id: uuid.UUID
    ) -> Order:
        order = self.get_order(order_id)
        if payload.to_status not in VALID_TRANSITIONS.get(order.status, set()):
            raise InvalidStateTransitionError(
                f"Cannot transition order from {order.status.value} to {payload.to_status.value}."
            )

        self.repository.record_status_change(
            OrderStatusHistory(
                order_id=order.id,
                from_status=order.status.value,
                to_status=payload.to_status.value,
                changed_by_staff_id=staff_user_id,
                notes=payload.notes,
            )
        )
        order.status = payload.to_status
        return self.repository.save(order)
