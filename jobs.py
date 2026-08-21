# Job functions - what actually runs, regardless of how it gets called.
#
# Currently called via FastAPI's BackgroundTasks (routers/auth.py's
# forgot_password: background_tasks.add_task(send_password_reset_email, ...))
# - runs in the same process as the API, after the response is sent, no
# separate worker needed. Chosen for the pilot launch since Render's
# Background Worker service type has no free tier - see CLAUDE.md's
# "Background workers" section for the full trade-off.
#
# redis_queue.py/worker.py still exist and still work (RQ + a real
# separate worker process) if this project ever needs a job to survive a
# process restart or wants genuine cross-process queueing - nothing about
# this function itself would need to change to switch back; only the
# call site (job_queue.enqueue(...) instead of background_tasks.add_task(...)).

import logging
import os

from jinja2 import Environment, FileSystemLoader

import email_client

logger = logging.getLogger(__name__)

# Frontend page the reset link points to - same "localhost placeholder
# until a real frontend URL exists" pattern as pesapal_client.py's
# PESAPAL_CALLBACK_URL.
PASSWORD_RESET_URL = os.getenv("PASSWORD_RESET_URL", "http://localhost:3000/reset-password")

# Where the "Log in to the staff dashboard" link in staff-facing emails
# points - same placeholder pattern as PASSWORD_RESET_URL above until
# Nyson's staff dashboard has a real login page to link to.
STAFF_DASHBOARD_URL = os.getenv("STAFF_DASHBOARD_URL", "http://localhost:3000/staff/login")

# Built ONCE at import time, not re-created on every single email sent -
# same "set up shared config once at module level" idea as this project's
# other wrapper modules. os.path.dirname(__file__) gives the FOLDER this
# file (jobs.py) itself lives in, then joins "templates" onto it - using
# an absolute path here (not just the string "templates") matters because
# a plain relative path is read relative to wherever the process happened
# to be STARTED from, not from wherever jobs.py sits on disk; this way it
# finds templates/ correctly no matter what directory uvicorn was launched
# from.
_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
_template_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR))


def send_password_reset_email(email: str, reset_token: str, full_name: str | None = None) -> None:
    # The actual link the customer clicks - the frontend page reads the
    # `token` query param and calls POST /auth/password/reset with it.
    reset_link = f"{PASSWORD_RESET_URL}?token={reset_token}"
    subject = "Reset Your JN Electronics Password"
    # full_name is optional (a guest-turned-customer row might not have
    # one) - "there" reads naturally either way ("Hi there," vs "Hi Jane,").
    greeting_name = full_name or "there"

    body = (
        f"Hi {greeting_name},\n\n"
        "We received a request to reset your JN Electronics password.\n\n"
        f"Click the link below to choose a new password:\n{reset_link}\n\n"
        "If you didn't request this, you can safely ignore this email."
    )
    # get_template() reads templates/password_reset.html; .render() fills
    # in its {{ full_name }}/{{ reset_link }} placeholders with the real
    # values and returns the finished HTML as a plain string.
    html = _template_env.get_template("password_reset.html").render(
        full_name=greeting_name, reset_link=reset_link
    )

    # Same graceful-degradation idea as pesapal_client.is_configured() -
    # checked by routers/payments.py before attempting a real payment.
    # Here it matters most for a fresh clone of this project (no Resend
    # credentials in .env yet) - the job logs the link instead of crashing,
    # so password reset is still testable without real email set up.
    if not email_client.is_configured():
        logger.info("Resend not configured - password reset link for %s: %s", email, reset_link)
        return

    try:
        email_client.send_email(email, subject, body, html=html)
    except email_client.EmailError:
        # logger.exception (not .info) - this is a genuine infrastructure
        # failure (Resend unreachable, bad credentials), not an expected
        # business outcome, so it SHOULD show up as a Sentry Issue, not
        # just a breadcrumb. exception() automatically includes the
        # traceback, unlike a plain logger.error(...) call.
        logger.exception("Failed to send password reset email to %s", email)
        return

    # logger.info (not print) - this is what makes the job's activity show
    # up in Sentry too (see observability.py), not just this process's
    # console.
    logger.info("Password reset email sent to %s", email)


def send_order_confirmation_email(
    email: str,
    full_name: str,
    order_number: str,
    items: list[dict],
    subtotal: float,
    total: float,
    delivery_address: str,
) -> None:
    # Spec'd in docs/JN_API_Specification.md's checkout sequence diagram
    # (§4.2: "Enqueue send_order_confirmation_email") - only called by
    # routers/orders.py's checkout, and only when the order actually has
    # an email to send to (guest_email is optional - a phone-only guest
    # checkout has nothing for this job to send to, so checkout() simply
    # doesn't enqueue it in that case, rather than this function silently
    # no-op-ing on an empty string).
    #
    # Takes plain values (a list of dicts, not ORM Item objects), same
    # reasoning as send_password_reset_email taking plain strings: this
    # runs from a BackgroundTasks callback AFTER the request's own DB
    # session has already been closed, so it can't safely touch ORM
    # objects tied to that session - only plain Python values survive that
    # boundary safely.
    subject = f"Your JN Electronics Order {order_number} Is Confirmed"

    item_lines = "\n".join(
        f"- {item['name']}"
        + (f" ({item['variant_label']})" if item.get("variant_label") else "")
        + f" x{item['quantity']} - UGX {item['line_total']:,.2f}"
        for item in items
    )
    body = (
        f"Hi {full_name},\n\n"
        f"Thanks for your order! {order_number} has been placed and is now being processed.\n\n"
        f"Items:\n{item_lines}\n\n"
        f"Subtotal: UGX {subtotal:,.2f}\n"
        f"Total: UGX {total:,.2f}\n\n"
        f"Delivering to: {delivery_address}\n\n"
        "We'll notify you as your order's status changes."
    )
    # Money gets formatted to a display string ("50,000.00") BEFORE going
    # into the template - Jinja2 can format numbers too, but doing it here
    # in Python keeps the template itself simple (just print the string),
    # and keeps the exact same formatting logic in one place for both the
    # plain-text body above and the HTML below.
    html = _template_env.get_template("order_confirmation.html").render(
        full_name=full_name,
        order_number=order_number,
        items=[{**item, "line_total": f"{item['line_total']:,.2f}"} for item in items],
        subtotal=f"{subtotal:,.2f}",
        total=f"{total:,.2f}",
        delivery_address=delivery_address,
    )

    if not email_client.is_configured():
        logger.info("Resend not configured - order confirmation for %s not sent (order %s)", email, order_number)
        return

    try:
        email_client.send_email(email, subject, body, html=html)
    except email_client.EmailError:
        logger.exception("Failed to send order confirmation email to %s for order %s", email, order_number)
        return

    logger.info("Order confirmation email sent to %s for order %s", email, order_number)


def notify_staff_new_order(
    staff_emails: list[str],
    order_number: str,
    customer_name: str,
    customer_email: str | None,
    district: str,
    total: float,
    delivery_address: str,
) -> None:
    # Spec'd alongside send_order_confirmation_email in the same sequence
    # diagram ("Enqueue notify_staff_new_order") - fires for EVERY active
    # staff member, regardless of role (a deliberate, simple choice - not
    # every staff role even manages orders day to day, but
    # the docs don't specify a narrower audience, and "everyone sees it"
    # is easy to reason about and safe to start with).
    if not staff_emails:
        # Nothing to do - not an error, just no active staff to notify
        # (shouldn't happen in practice; the seeded System Administrator
        # alone would normally make this non-empty).
        return

    subject = f"New Order {order_number} Placed"
    body = (
        "A new order has been placed.\n\n"
        f"Order: {order_number}\n"
        f"Customer: {customer_name}\n"
        f"Email: {customer_email or 'N/A'}\n"
        f"District: {district}\n"
        f"Total: UGX {total:,.2f}\n"
        f"Delivering to: {delivery_address}\n\n"
        "Log in to the staff dashboard to process it."
    )
    html = _template_env.get_template("staff_new_order.html").render(
        order_number=order_number,
        customer_name=customer_name,
        customer_email=customer_email or "N/A",
        district=district,
        total=f"{total:,.2f}",
        delivery_address=delivery_address,
        dashboard_url=STAFF_DASHBOARD_URL,
    )

    if not email_client.is_configured():
        logger.info("Resend not configured - new-order notification for order %s not sent", order_number)
        return

    # One send per staff member, not one email with everyone in "to" -
    # a shared recipient list would expose every staff member's email
    # address to every OTHER staff member, the same privacy reasoning BCC
    # exists for. A failed send to one staff member logs and continues
    # rather than aborting the rest of the list.
    sent_count = 0
    for staff_email in staff_emails:
        try:
            email_client.send_email(staff_email, subject, body, html=html)
            sent_count += 1
        except email_client.EmailError:
            logger.exception("Failed to send new-order notification to %s for order %s", staff_email, order_number)

    logger.info("New-order notification sent to %d/%d staff for order %s", sent_count, len(staff_emails), order_number)


def notify_staff_payment_received(
    staff_emails: list[str],
    order_number: str,
    customer_name: str,
    customer_email: str | None,
    amount: float,
    provider: str,
) -> None:
    # Staff-facing counterpart to send_payment_confirmed_email (which goes
    # to the CUSTOMER) - fires to every active staff member once a payment
    # actually resolves to PAID, same "everyone sees it" reasoning as
    # notify_staff_new_order above.
    if not staff_emails:
        return

    subject = f"Payment Received for Order {order_number}"
    body = (
        "A payment has been received.\n\n"
        f"Order: {order_number}\n"
        f"Customer: {customer_name}\n"
        f"Email: {customer_email or 'N/A'}\n"
        f"Amount: UGX {amount:,.2f}\n"
        f"Payment method: {provider}\n\n"
        "Log in to the staff dashboard to process this order."
    )
    html = _template_env.get_template("staff_payment_received.html").render(
        order_number=order_number,
        customer_name=customer_name,
        customer_email=customer_email,
        amount=f"{amount:,.2f}",
        provider=provider,
        dashboard_url=STAFF_DASHBOARD_URL,
    )

    if not email_client.is_configured():
        logger.info("Resend not configured - staff payment notification for order %s not sent", order_number)
        return

    sent_count = 0
    for staff_email in staff_emails:
        try:
            email_client.send_email(staff_email, subject, body, html=html)
            sent_count += 1
        except email_client.EmailError:
            logger.exception("Failed to send payment notification to %s for order %s", staff_email, order_number)

    logger.info("Payment notification sent to %d/%d staff for order %s", sent_count, len(staff_emails), order_number)


def send_staff_welcome_email(
    email: str,
    full_name: str,
    temporary_password: str,
    role: str,
) -> None:
    # Fires from routers/staff.py's create_staff, once a Owner/System
    # Administrator successfully creates a new staff account (frontend
    # handoff doc, 2026-08-18 - "Account created. An email with login
    # details was sent to {email}."). temporary_password is the PLAINTEXT
    # password the creator typed into the create-staff form - the only
    # place in this codebase a raw password is ever handed to a job like
    # this, since the whole point is the new hire needs to actually read
    # it once, in this one email, before it's gone (only the hash is kept
    # in the DB after this).
    subject = "Your JN Electronics Staff Account"
    body = (
        f"Hi {full_name},\n\n"
        "An account has been created for you on the JN Electronics admin dashboard.\n\n"
        f"Login email: {email}\n"
        f"Temporary password: {temporary_password}\n"
        f"Role: {role}\n\n"
        f"Log in here: {STAFF_DASHBOARD_URL}\n\n"
        "You'll be asked to choose a new password the first time you sign in - "
        "the temporary one above only works for that first login."
    )
    html = _template_env.get_template("staff_welcome.html").render(
        full_name=full_name,
        email=email,
        temporary_password=temporary_password,
        role=role,
        login_url=STAFF_DASHBOARD_URL,
    )

    if not email_client.is_configured():
        logger.info("Resend not configured - welcome email for new staff %s not sent", email)
        return

    try:
        email_client.send_email(email, subject, body, html=html)
    except email_client.EmailError:
        # logger.exception, not .info - a new hire silently never
        # receiving their login details is a real problem someone should
        # see, not just an expected business outcome.
        logger.exception("Failed to send welcome email to new staff %s", email)
        return

    logger.info("Welcome email sent to new staff %s", email)


def send_payment_confirmed_email(
    email: str,
    full_name: str,
    order_number: str,
    amount: float,
    provider: str,
) -> None:
    # NOT in the original spec's checkout sequence diagram (that one only
    # covers send_order_confirmation_email/notify_staff_new_order at ORDER
    # placement, before any payment exists) - added afterward because an
    # order being PLACED and an order being PAID FOR are genuinely
    # different events or a customer, worth its own confirmation. Fires
    # from routers/payments.py's payment_webhook, only when a payment
    # attempt actually resolves to PaymentStatus.PAID.
    subject = f"Payment Received for Order {order_number}"
    body = (
        f"Hi {full_name},\n\n"
        f"We've received your payment for order {order_number}. Your order is confirmed "
        "and will be processed shortly.\n\n"
        f"Amount paid: UGX {amount:,.2f}\n"
        f"Payment method: {provider}\n\n"
        "Thanks for shopping with JN Electronics."
    )
    html = _template_env.get_template("payment_confirmed.html").render(
        full_name=full_name,
        order_number=order_number,
        amount=f"{amount:,.2f}",
        provider=provider,
    )

    if not email_client.is_configured():
        logger.info("Resend not configured - payment confirmation for %s not sent (order %s)", email, order_number)
        return

    try:
        email_client.send_email(email, subject, body, html=html)
    except email_client.EmailError:
        logger.exception("Failed to send payment confirmation email to %s for order %s", email, order_number)
        return

    logger.info("Payment confirmation email sent to %s for order %s", email, order_number)
