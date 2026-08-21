# Checkout (cart -> order) and the customer-facing order endpoints, plus
# staff-only status advancement and its order_status_history audit log.

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import or_
from sqlalchemy.orm import Session

from audit import write_audit_log
from database import get_db
from envelope import EnvelopeRoute
from jobs import notify_staff_new_order, send_order_confirmation_email
from models import (
    Cart,
    CartItem,
    CartStatus,
    Customer,
    InventoryMovement,
    InventoryRecord,
    MovementType,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    Product,
    ProductVariant,
    StaffRole,
    StaffUser,
)
from pagination import build_pagination_meta
from routers.cart import get_current_cart
from routers.inventory import InsufficientInventoryError
from schemas import CheckoutRequest, OrderAddressUpdate, OrderItemRead, OrderRead, OrderStatusHistoryRead, OrderStatusUpdate, PaginatedResponse
from security import (
    bearer_scheme,
    decode_token_claims,
    get_current_customer,
    get_current_customer_or_admin,
    require_staff_role,
)

router = APIRouter(prefix="/orders", tags=["orders"], route_class=EnvelopeRoute)

logger = logging.getLogger(__name__)


class InvalidStateTransitionError(Exception):
    # Same pattern as InsufficientInventoryError/DuplicatePaymentError - a
    # plain exception, turned into a 409 by the handler registered in
    # main.py.
    pass


# Mirrors the order_status lifecycle from the DB design doc §6.1:
# pending -> confirmed -> packed -> out_for_delivery -> delivered.
# CANCELLED is deliberately absent from every set here, even
# pending/confirmed's - cancelling ALWAYS goes through
# PATCH /orders/{id}/cancel instead (the only route that also restores
# reserved inventory), never through this forward-progress endpoint. See
# advance_order_status's explicit to_status==CANCELLED check below for
# the friendlier error that points callers there. Delivered/cancelled are
# terminal either way - no further transition is ever valid from either.
VALID_STATUS_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED},
    OrderStatus.CONFIRMED: {OrderStatus.PACKED},
    OrderStatus.PACKED: {OrderStatus.OUT_FOR_DELIVERY},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}

STATUS_ADVANCE_ROLES = (StaffRole.SALES_ATTENDANT, StaffRole.OWNER)


def _resolve_order_actor(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    # Same "either a customer or any staff role" pattern as
    # routers/payments.py's _resolve_actor - status-history visibility
    # follows the same "Owning Customer or Staff" rule payments do.
    invalid_token = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    claims = decode_token_claims(credentials.credentials)
    if claims is None:
        raise invalid_token

    if claims.get("type") == "customer":
        customer = db.get(Customer, uuid.UUID(claims["sub"]))
        if customer is None:
            raise invalid_token
        return "customer", customer

    if claims.get("type") == "staff":
        staff = db.get(StaffUser, uuid.UUID(claims["sub"]))
        if staff is None or not staff.is_active:
            raise invalid_token
        return "staff", staff

    raise invalid_token

# Statuses in which an order can still be changed by the customer -
# per the docs, editing/cancelling stops being allowed once it's out for
# delivery (it might already be on a bike heading to the customer).
EDITABLE_STATUSES = {OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PACKED}


def _generate_order_number(db: Session) -> str:
    # Format: JN-YYYYMMDD-NNNN, sequential within the day. Counting
    # today's existing orders isn't perfectly race-safe under concurrent
    # checkouts, but that level of hardening is a later-phase concern, not
    # something to solve while still learning the basics.
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    count_today = db.query(Order).filter(Order.order_number.like(f"JN-{today}-%")).count()
    return f"JN-{today}-{count_today + 1:04d}"


def _find_short_items(db: Session, cart_items: list[CartItem]) -> list[dict]:
    # There's only ever one global InventoryRecord per variant now (no more
    # branches to search across) - this just checks each cart item against
    # that single stock row and reports any that don't have enough.
    short_items = []
    for item in cart_items:
        record = db.query(InventoryRecord).filter(InventoryRecord.variant_id == item.variant_id).first()
        available = record.quantity_available if record is not None else 0
        if available < item.quantity:
            short_items.append(
                {"variant_id": str(item.variant_id), "requested": item.quantity, "available": available}
            )
    return short_items


def _build_order_read(order: Order, db: Session) -> OrderRead:
    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    return OrderRead(
        id=order.id,
        order_number=order.order_number,
        customer_id=order.customer_id,
        guest_full_name=order.guest_full_name,
        guest_phone_number=order.guest_phone_number,
        guest_email=order.guest_email,
        delivery_address=order.delivery_address,
        district=order.district,
        status=order.status,
        requires_prepayment=order.requires_prepayment,
        subtotal=order.subtotal,
        total=order.total,
        created_at=order.created_at,
        updated_at=order.updated_at,
        items=[OrderItemRead.model_validate(i) for i in items],
    )


@router.post("", response_model=OrderRead)
def checkout(
    request: CheckoutRequest,
    background_tasks: BackgroundTasks,
    cart: Cart = Depends(get_current_cart),
    db: Session = Depends(get_db),
):
    cart_items = db.query(CartItem).filter(CartItem.cart_id == cart.id).all()
    if not cart_items:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cart is empty")

    short_items = _find_short_items(db, cart_items)
    if short_items:
        # warning, not error - this is a real business event worth having
        # visibility into (repeated occurrences might mean a restocking
        # problem), but it's an EXPECTED outcome the app already handles
        # cleanly (409 via InsufficientInventoryError), not a bug. Sentry's
        # LoggingIntegration only turns ERROR+ into its own Issue by
        # default, so this becomes a breadcrumb/log entry, not a false-alarm
        # error notification.
        logger.warning("Checkout failed: not enough stock for cart %s", cart.id)
        raise InsufficientInventoryError(
            "Not enough stock to fulfill this order",
            short_items=short_items,
        )

    # Build the order's line items BEFORE touching inventory, so if
    # anything here fails, we haven't already decremented stock we then
    # have no order to justify.
    order_items = []
    # Plain dicts, built alongside order_items above - what
    # send_order_confirmation_email actually gets handed (see jobs.py for
    # why: it runs after this request's DB session has closed, so it can
    # only safely receive plain values, not the OrderItem ORM objects
    # themselves).
    item_summaries = []
    subtotal = 0.0
    for item in cart_items:
        variant = db.get(ProductVariant, item.variant_id)
        product = db.get(Product, variant.product_id)
        line_total = item.quantity * item.unit_price_snapshot
        subtotal += line_total
        order_items.append(
            OrderItem(
                variant_id=item.variant_id,
                product_name_snapshot=product.name,
                variant_label_snapshot=variant.variant_label,
                quantity=item.quantity,
                unit_price=item.unit_price_snapshot,
                line_total=line_total,
            )
        )
        item_summaries.append(
            {
                "name": product.name,
                "variant_label": variant.variant_label,
                "quantity": item.quantity,
                "line_total": line_total,
            }
        )

    new_order = Order(
        order_number=_generate_order_number(db),
        customer_id=cart.customer_id,
        # NULL for a registered customer's cart, a real value for a
        # guest's - see the model's comment. This is what lets a guest
        # prove ownership of this order later, when paying for it
        # (routers/payments.py), since they have no account/login at all.
        guest_token=cart.guest_token,
        guest_full_name=request.guest_full_name,
        guest_phone_number=request.guest_phone_number,
        guest_email=request.guest_email,
        delivery_address=request.delivery_address,
        district=request.district,
        subtotal=subtotal,
        total=subtotal,  # no tax/delivery fee/discount logic yet
    )
    db.add(new_order)
    db.flush()  # assigns new_order.id without fully committing yet, so
    # the order_items below can reference a real order_id

    for order_item in order_items:
        order_item.order_id = new_order.id
        db.add(order_item)

    # Decrement each item's single global stock row now that the order is
    # committed to existing.
    for item in cart_items:
        record = db.query(InventoryRecord).filter(InventoryRecord.variant_id == item.variant_id).first()
        record.quantity_available -= item.quantity
        # staff_user_id=None - a checkout is system-generated (the docs
        # explicitly call this out: null for system-generated 'sold'
        # movements), there's no staff actor to attribute it to.
        db.add(
            InventoryMovement(
                inventory_record_id=record.id,
                movement_type=MovementType.SOLD,
                quantity_changed=-item.quantity,
                staff_user_id=None,
                order_id=new_order.id,
            )
        )

    # The cart that was just checked out is done being "active" - a later
    # add-to-cart call will create a fresh one.
    cart.status = CartStatus.CONVERTED

    db.commit()
    db.refresh(new_order)
    logger.info("Order %s placed (total=%s)", new_order.order_number, new_order.total)

    # Spec'd in docs/JN_API_Specification.md's checkout sequence diagram
    # (§4.2) - both run AFTER this response is sent (see jobs.py's module
    # docstring for the BackgroundTasks vs RQ trade-off this project made).
    #
    # Only enqueued when there's actually an email to send to - guest_email
    # is optional (schemas.py's CheckoutRequest), so a phone-only guest
    # checkout has nothing for this job to send to.
    if request.guest_email:
        background_tasks.add_task(
            send_order_confirmation_email,
            request.guest_email,
            request.guest_full_name,
            new_order.order_number,
            item_summaries,
            subtotal,
            new_order.total,
            request.delivery_address,
        )

    # Every active staff member, regardless of role - queried NOW (this
    # request's db session is still open) rather than inside the job
    # itself, keeping notify_staff_new_order free of any DB dependency,
    # same reasoning send_order_confirmation_email above takes plain
    # values instead of ORM objects.
    active_staff_emails = [
        email for (email,) in db.query(StaffUser.email).filter(StaffUser.is_active == True).all()  # noqa: E712
    ]
    background_tasks.add_task(
        notify_staff_new_order,
        active_staff_emails,
        new_order.order_number,
        request.guest_full_name,
        request.guest_email,
        request.district,
        new_order.total,
        request.delivery_address,
    )

    return _build_order_read(new_order, db)


@router.get("", response_model=PaginatedResponse[OrderRead])
def list_my_orders(
    skip: int = 0,
    limit: int = 10,
    current_customer=Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    query = db.query(Order).filter(Order.customer_id == current_customer.id)
    total = query.count()
    orders = query.offset(skip).limit(limit).all()
    return PaginatedResponse[OrderRead](
        items=[_build_order_read(o, db) for o in orders],
        pagination=build_pagination_meta(skip, limit, total),
    )


# Not in the original spec - GET "" above is scoped to "my orders" (a
# customer's own), and there was previously no way for staff to browse
# ALL orders at all - the only staff-visible list was the admin
# dashboard's recent-orders widget, an unfiltered capped feed never meant
# to support real fulfilment work. A separate path (not overloading GET ""
# with different behavior depending on who's asking) keeps "what does this
# endpoint return" unambiguous regardless of caller.
@router.get("/staff", response_model=PaginatedResponse[OrderRead])
def list_all_orders_staff(
    skip: int = 0,
    limit: int = 10,
    order_status: OrderStatus | None = None,
    search: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    _current_staff: StaffUser = Depends(require_staff_role(*STATUS_ADVANCE_ROLES)),
    db: Session = Depends(get_db),
):
    query = db.query(Order)

    if order_status is not None:
        query = query.filter(Order.status == order_status)
    if search:
        # order number OR the guest phone number on file - staff typically
        # have one or the other in hand (a customer reading out their
        # order number, or a phone number from a delivery note), not
        # necessarily both.
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(Order.order_number.ilike(like_pattern), Order.guest_phone_number.ilike(like_pattern))
        )
    if start_date is not None:
        query = query.filter(Order.created_at >= start_date)
    if end_date is not None:
        query = query.filter(Order.created_at <= end_date)

    query = query.order_by(Order.created_at.desc())
    total = query.count()
    orders = query.offset(skip).limit(limit).all()
    return PaginatedResponse[OrderRead](
        items=[_build_order_read(o, db) for o in orders],
        pagination=build_pagination_meta(skip, limit, total),
    )


@router.get("/{order_id}", response_model=OrderRead)
def read_order(
    order_id: uuid.UUID,
    # _resolve_order_actor (defined above) accepts EITHER a customer JWT
    # or a staff JWT of ANY role - same "Owning Customer or Staff" rule
    # this file's own status-history endpoint already uses. Widened from
    # admin-only (which used get_current_customer_or_admin) to match:
    # Sales Attendant/Owner need to load an order's detail to
    # work status transitions on it, the same way they can already list
    # every order (GET /orders/staff) and read its history.
    actor=Depends(_resolve_order_actor),
    db: Session = Depends(get_db),
):
    actor_type, actor_account = actor
    order = db.get(Order, order_id)
    # Same 404 whether the order truly doesn't exist or just isn't the
    # caller's - confirming "it exists but isn't yours" would leak
    # information a non-owner shouldn't get. The admin actor skips this
    # check entirely - that's the whole point of the bypass.
    if order is None or (actor_type == "customer" and order.customer_id != actor_account.id):
        raise HTTPException(status_code=404, detail="Order not found")
    return _build_order_read(order, db)


@router.patch("/{order_id}", response_model=OrderRead)
def update_order_address(
    order_id: uuid.UUID,
    update: OrderAddressUpdate,
    # Owning customer, or System Administrator specifically - see
    # get_current_customer_or_admin's docstring in security.py.
    actor=Depends(get_current_customer_or_admin),
    db: Session = Depends(get_db),
):
    actor_type, actor_account = actor
    order = db.get(Order, order_id)
    if order is None or (actor_type == "customer" and order.customer_id != actor_account.id):
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order can no longer be edited (status: {order.status.value})",
        )

    order.delivery_address = update.delivery_address
    # Optional - only touched if actually provided, so a caller that only
    # wants to change the address doesn't have to resend the existing
    # contact details just to avoid accidentally clearing them.
    if update.guest_full_name is not None:
        order.guest_full_name = update.guest_full_name
    if update.guest_phone_number is not None:
        order.guest_phone_number = update.guest_phone_number
    if update.guest_email is not None:
        order.guest_email = update.guest_email
    db.commit()
    db.refresh(order)
    return _build_order_read(order, db)


@router.patch("/{order_id}/cancel", response_model=OrderRead)
def cancel_order(
    order_id: uuid.UUID,
    # Owning customer, or System Administrator specifically - see
    # get_current_customer_or_admin's docstring in security.py.
    actor=Depends(get_current_customer_or_admin),
    db: Session = Depends(get_db),
):
    actor_type, actor_account = actor
    order = db.get(Order, order_id)
    if order is None or (actor_type == "customer" and order.customer_id != actor_account.id):
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order can no longer be cancelled (status: {order.status.value})",
        )

    # Give back the stock this order had reserved, since it's no longer
    # going to be fulfilled.
    order_items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    for item in order_items:
        record = db.query(InventoryRecord).filter(InventoryRecord.variant_id == item.variant_id).first()
        if record is not None:
            record.quantity_available += item.quantity
            # Closest fit among the docs' fixed movement_type set for "an
            # order was cancelled, its reserved stock is coming back" -
            # staff_user_id records the admin when THEY cancelled it on the
            # customer's behalf, None when the customer cancelled their own
            # order directly (matches every other audit-trail row in this
            # project: the actor who actually did it, not left blank).
            db.add(
                InventoryMovement(
                    inventory_record_id=record.id,
                    movement_type=MovementType.ADJUSTMENT,
                    quantity_changed=item.quantity,
                    reason="Order cancelled - stock restored",
                    staff_user_id=actor_account.id if actor_type == "admin" else None,
                    order_id=order.id,
                )
            )

    order.status = OrderStatus.CANCELLED
    db.commit()
    db.refresh(order)
    return _build_order_read(order, db)


@router.patch("/{order_id}/status", response_model=OrderRead)
def advance_order_status(
    order_id: uuid.UUID,
    update: OrderStatusUpdate,
    current_staff: StaffUser = Depends(require_staff_role(*STATUS_ADVANCE_ROLES)),
    db: Session = Depends(get_db),
):
    # Staff can act on ANY order here - unlike the customer-facing routes
    # above, there's no ownership check, just a real 404 if it doesn't exist.
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    # Cancelling never goes through this endpoint, regardless of the
    # order's current status - a clearer, purpose-built message than the
    # generic "invalid transition" one below, since this is a real caller
    # mistake (wrong endpoint), not an out-of-order lifecycle attempt.
    if update.to_status == OrderStatus.CANCELLED:
        raise InvalidStateTransitionError(
            "Cannot cancel an order through this endpoint - use PATCH /orders/{order_id}/cancel instead"
        )

    if update.to_status not in VALID_STATUS_TRANSITIONS[order.status]:
        raise InvalidStateTransitionError(
            f"Cannot transition order from '{order.status.value}' to '{update.to_status.value}'"
        )

    previous_status = order.status.value
    db.add(
        OrderStatusHistory(
            order_id=order.id,
            from_status=previous_status,
            to_status=update.to_status.value,
            changed_by_staff_id=current_staff.id,
            notes=update.notes,
        )
    )
    order.status = update.to_status
    write_audit_log(
        db,
        staff_user_id=current_staff.id,
        action="order.status_change",
        resource_type="order",
        resource_id=order.id,
        previous_value={"status": previous_status},
        new_value={"status": update.to_status.value},
    )
    db.commit()
    db.refresh(order)
    return _build_order_read(order, db)


@router.get("/{order_id}/status-history", response_model=list[OrderStatusHistoryRead])
def read_order_status_history(
    order_id: uuid.UUID,
    actor=Depends(_resolve_order_actor),
    db: Session = Depends(get_db),
):
    actor_type, actor_account = actor
    order = db.get(Order, order_id)
    # Same "identical 404 either way" privacy pattern as the rest of this
    # file - a non-owner customer can't distinguish "wrong id" from "not yours".
    if order is None or (actor_type == "customer" and order.customer_id != actor_account.id):
        raise HTTPException(status_code=404, detail="Order not found")

    # LEFT (outer) join, not an inner one - changed_by_staff_id is NOT NULL
    # on this table today (see the model's own comment: only staff-driven
    # transitions get logged here at all), but an outer join is what keeps
    # this query correct even if that ever changes, rather than silently
    # dropping a row whose staff account no longer joins cleanly.
    rows = (
        db.query(OrderStatusHistory, StaffUser.full_name)
        .outerjoin(StaffUser, OrderStatusHistory.changed_by_staff_id == StaffUser.id)
        .filter(OrderStatusHistory.order_id == order.id)
        .order_by(OrderStatusHistory.created_at)
        .all()
    )
    return [
        OrderStatusHistoryRead(
            id=history.id,
            order_id=history.order_id,
            from_status=history.from_status,
            to_status=history.to_status,
            changed_by_staff_id=history.changed_by_staff_id,
            changed_by_staff_name=staff_name,
            notes=history.notes,
            created_at=history.created_at,
        )
        for history, staff_name in rows
    ]
