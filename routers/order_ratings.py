# Post-delivery experience rating - "Rate your experience" link in the
# Order Delivered email (jobs.py's send_order_delivered_email). Added
# 2026-08-30 at the client's request, per Nyson's frontend doc: Option A
# (a public, token-based link - no login required, works for guest
# checkouts too), 1-5 stars + an optional 500-character comment, no
# per-dimension scores in this first version.
#
# Both routes are PUBLIC (no staff/customer auth at all) - the token
# itself IS the credential, the same way a password-reset link is. Split
# into prefill (GET, safe to call repeatedly, drives what the page shows
# BEFORE any submission) and submit (POST, the one-time write) so the
# frontend can render order context before asking for a score.

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Order, OrderItem, OrderRating, OrderStatus, OrderStatusHistory
from schemas import OrderRatingPrefill, OrderRatingRead, OrderRatingSubmit
from security import decode_order_rating_token

router = APIRouter(prefix="/public/order-ratings", tags=["order-ratings"], route_class=EnvelopeRoute)


def _resolve_rateable_order(token: str, db: Session) -> tuple[Order | None, bool]:
    # Shared by both routes below - decodes the token, then confirms the
    # order it points at genuinely exists AND is actually delivered. A
    # token could in principle decode fine but point at an order that
    # isn't delivered (shouldn't happen, since only advance_order_status's
    # DELIVERED branch ever issues one) - treated the same as "invalid"
    # rather than trusting the token's claim on its own.
    order_id_str, is_expired = decode_order_rating_token(token)
    if order_id_str is None:
        return None, is_expired

    try:
        order_id = uuid.UUID(order_id_str)
    except ValueError:
        # Can't happen with a token this app actually issued (the "sub"
        # claim is always str(order.id)) - guarding anyway since this is a
        # public, unauthenticated endpoint and a malformed "sub" should
        # fail safely as "invalid", not as an unhandled 500.
        return None, False

    order = db.get(Order, order_id)
    if order is None or order.status != OrderStatus.DELIVERED:
        return None, False
    return order, False


def _delivered_at(order: Order, db: Session):
    # The order_status_history row that moved this order INTO delivered -
    # falls back to the order's own updated_at if that row is somehow
    # missing (shouldn't happen for any order that reached DELIVERED
    # through the normal advance_order_status endpoint).
    history_row = (
        db.query(OrderStatusHistory)
        .filter(OrderStatusHistory.order_id == order.id, OrderStatusHistory.to_status == OrderStatus.DELIVERED.value)
        .order_by(OrderStatusHistory.created_at.desc())
        .first()
    )
    return history_row.created_at if history_row is not None else order.updated_at


@router.get("/{token}", response_model=OrderRatingPrefill)
def prefill_order_rating(token: str, db: Session = Depends(get_db)):
    # Always a 200 - "invalid"/"expired" are part of the response SHAPE
    # (rating_status), not an HTTP error, per Nyson's contract, since the
    # frontend renders its own error state for those from this same field
    # rather than branching on a status code.
    order, is_expired = _resolve_rateable_order(token, db)
    if order is None:
        return OrderRatingPrefill(rating_status="expired" if is_expired else "invalid")

    item_count = (
        db.query(func.sum(OrderItem.quantity)).filter(OrderItem.order_id == order.id).scalar() or 0
    )
    existing = db.query(OrderRating).filter(OrderRating.order_id == order.id).first()

    if existing is not None:
        return OrderRatingPrefill(
            rating_status="already_rated",
            order_number=order.order_number,
            delivered_at=_delivered_at(order, db),
            item_count=item_count,
            score=existing.score,
            comment=existing.comment,
            submitted_at=existing.created_at,
        )

    return OrderRatingPrefill(
        rating_status="eligible",
        order_number=order.order_number,
        delivered_at=_delivered_at(order, db),
        item_count=item_count,
    )


@router.post("/{token}", response_model=OrderRatingRead, status_code=status.HTTP_201_CREATED)
def submit_order_rating(token: str, submission: OrderRatingSubmit, db: Session = Depends(get_db)):
    # Unlike prefill above, submit is only ever reached after the frontend
    # has already shown an "eligible" prefill - a real HTTP error here
    # (rather than a 200 with a status field) is appropriate, since there's
    # no form left on screen for a plain error response to break.
    order, is_expired = _resolve_rateable_order(token, db)
    if order is None:
        if is_expired:
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="This rating link has expired")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rating link not found")

    existing = db.query(OrderRating).filter(OrderRating.order_id == order.id).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This order has already been rated")

    new_rating = OrderRating(order_id=order.id, score=submission.score, comment=submission.comment)
    db.add(new_rating)
    try:
        db.commit()
    except IntegrityError:
        # Race: two near-simultaneous submits for the same order (e.g. the
        # link opened in two tabs) - order_ratings.order_id's own
        # unique=True constraint is the real backstop this falls back on.
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This order has already been rated")
    db.refresh(new_rating)

    return OrderRatingRead(
        id=new_rating.id,
        order_id=new_rating.order_id,
        score=new_rating.score,
        comment=new_rating.comment,
        submitted_at=new_rating.created_at,
    )
