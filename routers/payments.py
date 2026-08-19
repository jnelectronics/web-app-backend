# Payment attempts against an order, backed by the REAL PesaPal API 3.0
# gateway (pesapal_client.py) - no longer simulated. initiate_payment calls
# PesaPal's SubmitOrderRequest and returns a redirect_url the frontend
# sends the customer's browser to (PesaPal's own hosted checkout page).
#
# /payments/webhook is PesaPal's IPN callback. It's on a SEPARATE router
# (webhook_router, no route_class=EnvelopeRoute) - PesaPal's own systems
# parse this response directly and expect an exact JSON shape (see
# payment_webhook below), not our API's usual {"success","message","data"}
# envelope. Wrapping it would break PesaPal's ability to read it.

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from jobs import notify_staff_payment_received, send_payment_confirmed_email
from models import Customer, Order, OrderStatus, Payment, PaymentStatus, StaffRole, StaffUser
from pesapal_client import PesaPalError, get_transaction_status, is_configured, submit_order_request
from schemas import PaymentInitiate, PaymentProvider, PaymentRead
from security import decode_token_claims, require_staff_role

router = APIRouter(tags=["payments"], route_class=EnvelopeRoute)

# A SEPARATE HTTPBearer instance from security.py's shared bearer_scheme,
# with auto_error=False - security.py's version REJECTS the request
# outright (403) the instant no Authorization header is present, before
# _resolve_actor below ever runs, which is exactly right for
# customer/staff-only endpoints but wrong here: a guest has no Bearer
# token at all, only an X-Guest-Token header, so this router needs to be
# able to receive "no Authorization header" as a normal case, not an
# automatic rejection, and decide for itself what that means.
_optional_bearer_scheme = HTTPBearer(auto_error=False)
webhook_router = APIRouter(tags=["payments"])

logger = logging.getLogger(__name__)

# Not in the original API spec - added as a deliberate safety net once this
# project switched to real PesaPal credentials (see initiate_payment below).
# A payment attempt older than this is treated as abandoned rather than
# still-in-flight, so a customer whose first attempt genuinely stalled
# (closed the tab, connection dropped) isn't locked out of retrying forever.
PENDING_PAYMENT_WINDOW_MINUTES = 15

# Same env var main.py's CORSMiddleware already reads - re-read here rather
# than imported from main.py, since main.py imports this router (importing
# back from main.py would be a circular import). This backend serves more
# than one real frontend origin at once (production + a separate QA/test
# domain) from a single deployment, so PesaPal's callback_url can't be one
# fixed value - see _resolve_callback_url below.
_ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
}


def _resolve_callback_url(http_request: Request) -> str | None:
    # The browser sends its own Origin header on the fetch/XHR call that
    # hits this endpoint - that's the ONE reliable signal for "which real
    # frontend site is this customer actually on". Only trusted when it's a
    # known origin (the same whitelist CORS already enforces) - a spoofed
    # Origin header can't be used to redirect a PesaPal payment somewhere
    # arbitrary, since PesaPal's callback_url would otherwise be a classic
    # open-redirect target. Returns None (pesapal_client's own env-var
    # default takes over) when the header is missing or unrecognized - e.g.
    # a direct server-to-server call, curl, or the test suite.
    origin = http_request.headers.get("origin")
    if origin is not None and origin in _ALLOWED_ORIGINS:
        return f"{origin}/confirm-payment"
    return None


class DuplicatePaymentError(Exception):
    # Raised when an order already has a successful payment - mirrors
    # InsufficientInventoryError's pattern (routers/inventory.py): a plain
    # exception class, turned into a clean 409 by the handler registered
    # in main.py, instead of every call site building its own HTTPException.
    pass


class PaymentInProgressError(Exception):
    # Raised when the order already has a RECENT unresolved payment attempt
    # (see PENDING_PAYMENT_WINDOW_MINUTES) - prevents a double-click or a
    # retried request from opening a SECOND real PesaPal checkout session
    # for the same order. This matters because our own DB-level uniqueness
    # (Payment's partial unique index, models.py) only stops us from
    # RECORDING two successful payments - it does nothing to stop PesaPal
    # from actually charging the customer twice if they went on to complete
    # two separate checkout sessions. Blocking the second session from ever
    # being created is the only way to prevent that real double-charge, not
    # just hide it after the fact.
    pass


class PaymentsUnavailableError(Exception):
    # Raised when PesaPal isn't configured yet (see pesapal_client.is_configured) -
    # e.g. while a real merchant account is still going through PesaPal's
    # business verification. Turned into a clean 503 with a friendly
    # customer-facing message by the handler in main.py, instead of every
    # checkout attempt failing deep inside a PesaPal HTTP error.
    pass


class PaymentGatewayError(Exception):
    # Raised when PesaPal itself rejects or fails a real request (e.g.
    # SubmitOrderRequest) - a genuine gateway failure, not this API being
    # broken. Turned into a 502 with error_code PAYMENT_GATEWAY_ERROR by the
    # handler in main.py, distinct from the generic INTERNAL_ERROR a plain
    # HTTPException(502, ...) would have produced - lets the frontend show
    # "the payment provider is having trouble" instead of a generic error.
    pass


def _resolve_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer_scheme),
    x_guest_token: str | None = Header(default=None, alias="X-Guest-Token"),
    db: Session = Depends(get_db),
) -> tuple[str, Customer | StaffUser | str]:
    # Payments accept THREE kinds of caller, not just two: a customer, any
    # staff role, or - since a guest checkout has no account/login at all
    # - a guest identified only by the SAME X-Guest-Token header cart
    # operations already use (routers/cart.py's get_current_cart). Returns
    # ("customer", Customer), ("staff", StaffUser), or ("guest", <the raw
    # token string>) - a guest resolves to no DB row of its own, since
    # there's no account to look up; whether it's actually allowed to see
    # a given order is decided by _get_visible_order below, by comparing
    # this string against that ONE order's own stored guest_token.
    invalid_token = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is not None:
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

    # No Bearer token at all - fall back to the guest header. An empty/
    # missing X-Guest-Token here means the caller sent NEITHER kind of
    # credential, so there's genuinely nothing to authenticate with.
    if x_guest_token:
        return "guest", x_guest_token

    raise invalid_token


def _get_visible_order(order_id: uuid.UUID, actor_type: str, actor, db: Session) -> Order:
    # Staff (any role - the docs don't restrict payments to a specific
    # role, unlike catalog writes) can see any order. A customer can only
    # see their own, and a guest only an order whose OWN stored
    # guest_token matches the one they sent - same "identical 404 either
    # way" privacy pattern routers/orders.py already uses, so a non-owner
    # (of any kind) can't tell the difference between "wrong order id" and
    # "not yours".
    order = db.get(Order, order_id)
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    if order is None:
        raise not_found
    if actor_type == "customer" and order.customer_id != actor.id:
        raise not_found
    if actor_type == "guest" and (order.guest_token is None or order.guest_token != actor):
        raise not_found
    return order


def _map_pesapal_status(payment_status_description: str) -> tuple[PaymentStatus, str | None]:
    # PesaPal has four possible statuses; this project's PaymentStatus enum
    # only has PENDING/AWAITING_PAYMENT/PAID/FAILED. REVERSED (a payment
    # that succeeded and was later reversed/refunded) collapses onto
    # FAILED here too - this project doesn't model refunds as their own
    # state yet, a deliberate scope line, not an oversight.
    #
    # .upper() is load-bearing, not defensive fluff - a real live sandbox
    # test returned "Completed" (Title Case), not "COMPLETED" as the docs'
    # examples show, and an exact-match comparison silently mapped a
    # genuinely successful payment to FAILED. Confirmed no test would have
    # caught this - the mocked unit tests control this value directly, so
    # they never exercise the real casing PesaPal actually sends.
    normalized = payment_status_description.upper()
    if normalized == "COMPLETED":
        return PaymentStatus.PAID, None
    if normalized == "REVERSED":
        return PaymentStatus.FAILED, "Payment was reversed"
    if normalized == "INVALID":
        return PaymentStatus.FAILED, "Payment is invalid"
    return PaymentStatus.FAILED, "Payment failed"


@router.post("/orders/{order_id}/payments", response_model=PaymentRead, status_code=status.HTTP_201_CREATED)
def initiate_payment(
    order_id: uuid.UUID,
    request: PaymentInitiate,
    http_request: Request,
    actor=Depends(_resolve_actor),
    db: Session = Depends(get_db),
):
    actor_type, actor_account = actor
    # Ownership check BEFORE anything provider-specific below - a caller
    # who can't see this order shouldn't learn anything about payment
    # availability for it either, same "identical 404 either way" privacy
    # reasoning _get_visible_order already applies elsewhere.
    order = _get_visible_order(order_id, actor_type, actor_account, db)

    # Provider-agnostic checks - an order shouldn't be payable twice, or
    # have two attempts in flight at once, REGARDLESS of how it's being
    # paid for, so these run before branching on request.provider below.
    already_paid = (
        db.query(Payment)
        .filter(Payment.order_id == order.id, Payment.status == PaymentStatus.PAID)
        .first()
    )
    if already_paid is not None:
        raise DuplicatePaymentError(f"Order {order.order_number} has already been paid")

    pending_cutoff = datetime.now(timezone.utc) - timedelta(minutes=PENDING_PAYMENT_WINDOW_MINUTES)
    pending_attempt = (
        db.query(Payment)
        .filter(
            Payment.order_id == order.id,
            Payment.status == PaymentStatus.AWAITING_PAYMENT,
            Payment.initiated_at >= pending_cutoff,
        )
        .first()
    )
    if pending_attempt is not None:
        raise PaymentInProgressError(
            f"A payment attempt for order {order.order_number} is already in progress. "
            "Please wait a few minutes, or check its status, before trying again."
        )

    # Not in the original spec (added 2026-08-06) - cash/pay-on-collection
    # never touches PesaPal at all: no redirect, no gateway session, just a
    # record that stock/fulfillment can proceed against, waiting for staff
    # to confirm the cash was actually collected (see mark_cash_payment_paid
    # below). PaymentStatus.PENDING is exactly this - "recorded, not yet
    # resolved, no gateway involved" - already in the enum, just never
    # actually reachable through any code path before this one.
    if request.provider == PaymentProvider.CASH_ON_DELIVERY:
        new_payment = Payment(
            order_id=order.id,
            provider=request.provider.value,
            amount=request.amount,
            status=PaymentStatus.PENDING,
            initiated_at=datetime.now(timezone.utc),
        )
        db.add(new_payment)
        db.commit()
        db.refresh(new_payment)
        logger.info("Cash-on-delivery payment %s recorded for order %s", new_payment.id, order.id)
        return new_payment

    if not is_configured():
        raise PaymentsUnavailableError("Payment services will be available soon. Please check back later.")

    new_payment = Payment(
        order_id=order.id,
        provider=request.provider.value,
        amount=request.amount,
        status=PaymentStatus.AWAITING_PAYMENT,
        initiated_at=datetime.now(timezone.utc),
    )
    db.add(new_payment)
    db.flush()  # assigns new_payment.id - used below as PesaPal's unique merchant reference

    # guest_full_name is a single field on Order (see routers/orders.py) -
    # naive split into first/last, since PesaPal's billing_address wants
    # them separately and this project never collected them that way.
    first_name, _, last_name = order.guest_full_name.partition(" ")

    try:
        pesapal_result = submit_order_request(
            merchant_reference=str(new_payment.id),
            amount=request.amount,
            currency="UGX",
            description=f"Order {order.order_number} ({request.provider.value})",
            billing_email=order.guest_email,
            billing_phone=order.guest_phone_number,
            billing_first_name=first_name,
            billing_last_name=last_name or first_name,
            callback_url=_resolve_callback_url(http_request),
        )
    except PesaPalError as exc:
        # logger.exception (not .info/.warning) - this is a genuine
        # infrastructure problem (PesaPal unreachable, rejected our
        # request, etc.), not an expected business outcome, so it SHOULD
        # become a real Sentry Issue worth being alerted on. exc_info is
        # attached automatically, giving the full traceback in Sentry.
        logger.exception("PesaPal SubmitOrderRequest failed for order %s", order.id)
        # Rolls back the Payment row too - if PesaPal never accepted the
        # order, there's nothing real for this row to represent.
        db.rollback()
        raise PaymentGatewayError(f"Payment gateway error: {exc}")

    new_payment.provider_reference = pesapal_result["order_tracking_id"]
    new_payment.redirect_url = pesapal_result["redirect_url"]
    db.commit()
    db.refresh(new_payment)
    logger.info(
        "Payment %s initiated for order %s (provider=%s, amount=%s)",
        new_payment.id,
        order.id,
        request.provider.value,
        request.amount,
    )
    return new_payment


@router.get("/orders/{order_id}/payments", response_model=list[PaymentRead])
def list_order_payments(
    order_id: uuid.UUID,
    actor=Depends(_resolve_actor),
    db: Session = Depends(get_db),
):
    actor_type, actor_account = actor
    order = _get_visible_order(order_id, actor_type, actor_account, db)
    return (
        db.query(Payment)
        .filter(Payment.order_id == order.id)
        .order_by(Payment.created_at)
        .all()
    )


@router.get("/payments/{payment_id}", response_model=PaymentRead)
def read_payment(
    payment_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    actor=Depends(_resolve_actor),
    db: Session = Depends(get_db),
):
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    actor_type, actor_account = actor
    # Re-run the same visibility check via the payment's order, so a
    # customer can't fetch someone else's payment just by guessing its id.
    _get_visible_order(payment.order_id, actor_type, actor_account, db)

    # Not in the original spec - added because the frontend polls exactly
    # this endpoint waiting for a payment to resolve (see
    # docs/Frontend_Integration_Contract.md), but until now the ONLY thing
    # that ever moved a payment out of AWAITING_PAYMENT was PesaPal's own
    # IPN callback actually reaching us. If that callback is ever missed
    # (a dropped webhook, a misconfigured IPN, a network hiccup on
    # PesaPal's side), the row - and the customer's polling frontend -
    # would be stuck showing "awaiting payment" forever even though
    # PesaPal itself already knows the real outcome. This makes a stuck
    # payment self-heal the next time anyone checks on it, by asking
    # PesaPal directly instead of only ever waiting to be told.
    #
    # Only ever PROMOTES to paid - never applies a FAILED outcome from
    # here (see _apply_pesapal_outcome's own comment for why: this runs on
    # every poll, including the very first one right after redirect,
    # before the customer may have even reached PesaPal's payment form,
    # and PesaPal has no distinct "still checking out" status to tell that
    # case apart from a genuine decline).
    if payment.status == PaymentStatus.AWAITING_PAYMENT and payment.provider_reference is not None:
        try:
            pesapal_status = get_transaction_status(payment.provider_reference)
        except PesaPalError:
            # Same reasoning as elsewhere: PesaPal itself being unreachable
            # is an infrastructure problem worth knowing about, but it
            # shouldn't fail this GET request - just fall back to
            # returning whatever status the row already had.
            logger.warning("PesaPal GetTransactionStatus recheck failed for payment %s", payment.id)
        else:
            new_status, failure_reason = _map_pesapal_status(pesapal_status["payment_status_description"])
            if new_status == PaymentStatus.PAID:
                _apply_pesapal_outcome(payment, new_status, failure_reason, db, background_tasks)

    return payment


# Same roles that can advance an order's fulfillment status
# (routers/orders.py's STATUS_ADVANCE_ROLES) - whoever's handling the
# order at collection/delivery time is who'd actually be confirming cash
# changed hands, not a separate permission concept.
MARK_CASH_PAID_ROLES = (StaffRole.SALES_ATTENDANT, StaffRole.OWNER)


@router.patch("/payments/{payment_id}/mark-paid", response_model=PaymentRead)
def mark_cash_payment_paid(
    payment_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    _current_staff: StaffUser = Depends(require_staff_role(*MARK_CASH_PAID_ROLES)),
    db: Session = Depends(get_db),
):
    # Not in the original spec - the staff-side half of cash-on-delivery
    # (see initiate_payment above): there's no gateway webhook for cash, so
    # a human has to be the one confirming it was actually collected.
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    if payment.provider != PaymentProvider.CASH_ON_DELIVERY.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only cash-on-delivery payments can be marked paid this way",
        )
    if payment.status != PaymentStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payment is not awaiting cash collection (status: {payment.status.value})",
        )

    payment.status = PaymentStatus.PAID
    payment.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(payment)

    order = db.get(Order, payment.order_id)
    if order.guest_email:
        # Same job the PesaPal webhook fires on a successful online
        # payment - the customer gets the same confirmation regardless of
        # how they actually paid.
        background_tasks.add_task(
            send_payment_confirmed_email,
            order.guest_email,
            order.guest_full_name,
            order.order_number,
            payment.amount,
            payment.provider,
        )

    active_staff_emails = [
        email for (email,) in db.query(StaffUser.email).filter(StaffUser.is_active == True).all()  # noqa: E712
    ]
    background_tasks.add_task(
        notify_staff_payment_received,
        active_staff_emails,
        order.order_number,
        order.guest_full_name,
        order.guest_email,
        payment.amount,
        payment.provider,
    )

    logger.info("Cash-on-delivery payment %s marked paid for order %s", payment.id, payment.order_id)
    return payment


def _apply_pesapal_outcome(
    payment: Payment,
    new_status: PaymentStatus,
    failure_reason: str | None,
    db: Session,
    background_tasks: BackgroundTasks,
) -> None:
    # Shared by BOTH the real IPN webhook below AND read_payment's lazy
    # recheck (added so a customer isn't stuck forever if PesaPal's IPN
    # callback never reaches us - see read_payment's own comment). Pulled
    # out into one function so there's exactly one place that decides what
    # ALREADY-DECIDED outcome means for our DB, instead of two copies of
    # this business logic that could quietly drift apart over time.
    #
    # Deliberately takes new_status/failure_reason already resolved by the
    # CALLER, rather than calling get_transaction_status/_map_pesapal_status
    # itself - the two callers are NOT allowed to treat a mapped result the
    # same way. The webhook only ever calls this after PesaPal's IPN told
    # us something genuinely changed, so PAID and FAILED are both fine to
    # apply. read_payment's lazy recheck runs on every poll - including the
    # very first one, seconds after redirect, before the customer may have
    # even reached PesaPal's payment form - and PesaPal has no "still in
    # progress" status of its own (_map_pesapal_status's fallback treats
    # anything that isn't COMPLETED/REVERSED/INVALID as FAILED). Letting
    # the lazy path apply a FAILED outcome could tell a customer their
    # payment failed while they're still legitimately mid-checkout - so it
    # only ever calls this function for a PAID outcome, never for FAILED.
    if new_status == PaymentStatus.PAID:
        # Defensive re-check: a DIFFERENT attempt for the same order could
        # have been confirmed paid in between this payment being created
        # and this check running. The partial unique index on the table is
        # the last line of defense against this actually violating
        # BR-PAY-005; this just avoids trying in the first place.
        already_paid = (
            db.query(Payment)
            .filter(Payment.order_id == payment.order_id, Payment.status == PaymentStatus.PAID)
            .first()
        )
        if already_paid is not None:
            return

    payment.status = new_status
    payment.failure_reason = failure_reason
    payment.completed_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError:
        # The SELECT re-check above can't fully close a genuine race: TWO
        # confirmations for TWO DIFFERENT payment attempts on the SAME
        # order can both pass that check before either commits, if they
        # land at almost the exact same instant. Only one commit can
        # actually win - the partial unique index rejects the other one
        # right here. That's the correct outcome (still only one PAID row,
        # BR-PAY-005 holds); this just makes sure the LOSING call still
        # resolves cleanly instead of surfacing a raw 500 from an
        # uncaught database error.
        db.rollback()
        logger.warning(
            "Payment %s lost a same-order PAID race to another attempt - order already paid", payment.id
        )
        return
    db.refresh(payment)

    # info, not warning, even for a FAILED outcome here - a declined card
    # is a normal, expected business event at real-world volume, not
    # something to flag for attention every time it happens.
    logger.info("Payment %s resolved: status=%s", payment.id, new_status.value)

    if new_status == PaymentStatus.PAID:
        # A DIFFERENT event from send_order_confirmation_email (fires at
        # ORDER placement, before any payment exists) - this one only
        # fires once money has actually been confirmed received. Only
        # enqueued when there's an email to send to, same optional-email
        # reasoning as checkout()'s order confirmation job.
        order = db.get(Order, payment.order_id)

        if order.status == OrderStatus.PENDING:
            # Prepaid order: payment came back PAID before any staff member
            # got a chance to manually confirm it - skip that step instead
            # of leaving a paid order sitting in "pending" until someone
            # notices. Deliberately NOT logged to order_status_history -
            # that table's changed_by_staff_id is NOT NULL (see its own
            # comment in models.py) because it only ever records
            # STAFF-driven transitions through the one PATCH /status
            # endpoint. This is a system-driven transition, same category
            # as a customer's own self-service cancel, which already skips
            # that table for the identical reason.
            order.status = OrderStatus.CONFIRMED
            db.commit()
            logger.info("Order %s auto-confirmed on payment %s", order.id, payment.id)

        if order.guest_email:
            background_tasks.add_task(
                send_payment_confirmed_email,
                order.guest_email,
                order.guest_full_name,
                order.order_number,
                payment.amount,
                payment.provider,
            )

        active_staff_emails = [
            email for (email,) in db.query(StaffUser.email).filter(StaffUser.is_active == True).all()  # noqa: E712
        ]
        background_tasks.add_task(
            notify_staff_payment_received,
            active_staff_emails,
            order.order_number,
            order.guest_full_name,
            order.guest_email,
            payment.amount,
            payment.provider,
        )


@webhook_router.get("/payments/webhook")
def payment_webhook(
    background_tasks: BackgroundTasks,
    order_tracking_id: str = Query(alias="OrderTrackingId"),
    order_merchant_reference: str = Query(alias="OrderMerchantReference"),
    order_notification_type: str = Query(default="IPNCHANGE", alias="OrderNotificationType"),
    db: Session = Depends(get_db),
):
    # PesaPal's IPN deliberately does NOT include the payment's actual
    # status in this callback (a security measure - see pesapal_client.py)
    # - just these tracking ids. Finding out what really happened means
    # calling GetTransactionStatus ourselves, below.

    # The exact acknowledgment shape PesaPal's own systems expect back -
    # not a response for OUR API's clients. status: 200 = "we handled it",
    # 500 = "something went wrong on our end, please retry."
    def ack(processing_status: int) -> dict:
        return {
            "orderNotificationType": order_notification_type,
            "orderTrackingId": order_tracking_id,
            "orderMerchantReference": order_merchant_reference,
            "status": processing_status,
        }

    payment = db.query(Payment).filter(Payment.provider_reference == order_tracking_id).first()
    if payment is None:
        # warning, not error - could be a stale/replayed callback (e.g.
        # from an old test) rather than a real bug, but still worth
        # knowing about if it happens a lot.
        logger.warning("PesaPal IPN for unknown order_tracking_id=%s", order_tracking_id)
        return ack(500)

    # Idempotent per PAYINT-005: once paid, any further callback (even a
    # legitimate duplicate) is a no-op - never reprocessed or reversed here.
    if payment.status == PaymentStatus.PAID:
        return ack(200)

    try:
        pesapal_status = get_transaction_status(order_tracking_id)
    except PesaPalError:
        # A real Sentry Issue, same reasoning as initiate_payment's
        # PesaPalError handling - PesaPal itself failing to answer a
        # status check is an infrastructure problem, not a business outcome.
        logger.exception("PesaPal GetTransactionStatus failed for payment %s", payment.id)
        return ack(500)

    new_status, failure_reason = _map_pesapal_status(pesapal_status["payment_status_description"])
    _apply_pesapal_outcome(payment, new_status, failure_reason, db, background_tasks)
    return ack(200)
