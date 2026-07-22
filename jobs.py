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

import email_client

logger = logging.getLogger(__name__)

# Frontend page the reset link points to - same "localhost placeholder
# until a real frontend URL exists" pattern as pesapal_client.py's
# PESAPAL_CALLBACK_URL.
PASSWORD_RESET_URL = os.getenv("PASSWORD_RESET_URL", "http://localhost:3000/reset-password")


def send_password_reset_email(email: str, reset_token: str) -> None:
    # The actual link the customer clicks - the frontend page reads the
    # `token` query param and calls POST /auth/password/reset with it.
    reset_link = f"{PASSWORD_RESET_URL}?token={reset_token}"
    subject = "Reset your JN Electronics password"
    body = (
        "We received a request to reset your JN Electronics password.\n\n"
        f"Click the link below to choose a new password:\n{reset_link}\n\n"
        "If you didn't request this, you can safely ignore this email."
    )

    # Same graceful-degradation idea as pesapal_client.is_configured() -
    # checked by routers/payments.py before attempting a real payment.
    # Here it matters most for a fresh clone of this project (no Gmail
    # credentials in .env yet) - the job logs the link instead of crashing,
    # so password reset is still testable without real email set up.
    if not email_client.is_configured():
        logger.info("Gmail not configured - password reset link for %s: %s", email, reset_link)
        return

    try:
        email_client.send_email(email, subject, body)
    except email_client.EmailError:
        # logger.exception (not .info) - this is a genuine infrastructure
        # failure (Gmail unreachable, bad credentials), not an expected
        # business outcome, so it SHOULD show up as a Sentry Issue, not
        # just a breadcrumb. exception() automatically includes the
        # traceback, unlike a plain logger.error(...) call.
        logger.exception("Failed to send password reset email to %s", email)
        return

    # logger.info (not print) - this is what makes the job's activity show
    # up in Sentry too (see observability.py), not just this process's
    # console.
    logger.info("Password reset email sent to %s", email)
