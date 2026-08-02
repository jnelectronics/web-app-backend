# Wraps sending an email through Resend's API - the real email provider now
# backing jobs.py's send_password_reset_email. Same wrapper-module idea as
# pesapal_client.py/cloudinary_client.py/google_auth_client.py: every
# Resend-specific detail (auth header, request shape, endpoint URL) lives
# in this one file, so jobs.py only ever calls send_email(to, subject,
# body) with plain strings - it doesn't need to know anything about Resend
# itself.
#
# This file used to wrap Gmail's SMTP server instead (see git history) -
# the swap from Gmail to Resend only ever touched what's INSIDE this file.
# jobs.py, routers/auth.py, and tests/conftest.py's mock_email fixture
# didn't need a single line changed, because they only ever depended on
# THIS file's send_email()/is_configured() functions, never on Gmail
# specifically. That's the entire point of wrapping an external service
# behind a small module like this one: the rest of the app depends on the
# shape of "send an email", not on which company actually sends it.
#
# Resend's send-email endpoint is one simple HTTP POST, so this uses httpx
# (already a dependency, for pesapal_client.py) rather than adding
# Resend's own SDK package as a new dependency - same reasoning
# pesapal_client.py already uses for skipping the `requests` library.

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
# Must be an address on a domain that's been VERIFIED with Resend (DNS
# records added, see .env.example's comment) - Resend rejects the send
# outright if the domain isn't verified, regardless of how correct the API
# key is.
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL")

RESEND_API_URL = "https://api.resend.com/emails"


def is_configured() -> bool:
    # Same "degrade gracefully instead of crashing" idea as the other
    # wrapper modules' is_configured() calls - lets jobs.py check up front
    # whether real credentials exist before attempting to send anything.
    return bool(RESEND_API_KEY) and bool(RESEND_FROM_EMAIL)


class EmailError(Exception):
    # Raised when the send itself fails (bad API key, unverified domain,
    # Resend rejects the request, etc.) - lets callers (jobs.py) catch one
    # specific exception type instead of needing to know Resend's error shape.
    pass


def send_email(to_email: str, subject: str, body: str, html: str | None = None) -> None:
    # `html` is optional (defaults to None) so any EXISTING caller passing
    # just (to_email, subject, body) keeps working unchanged - only jobs.py
    # currently passes a real html value, for the styled password-reset
    # template (templates/password_reset.html).
    #
    # Sending BOTH text and html (when html is given) rather than html
    # alone: some email clients and spam filters prefer/require a
    # plain-text part, and it's a real fallback if HTML rendering ever
    # fails for a given inbox - the customer still gets a working link
    # either way.
    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    if html is not None:
        payload["html"] = html

    # Network-level failures (Resend unreachable, DNS failure, timeout)
    # are deliberately NOT caught here and are left to propagate as raw
    # httpx exceptions - same convention pesapal_client.py already uses.
    # Only a response Resend actually SENT BACK gets turned into an
    # EmailError, since that's the case with a real, useful message to
    # wrap (why it rejected the request), not just "something went wrong".
    response = httpx.post(
        RESEND_API_URL,
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    if response.status_code >= 400:
        raise EmailError(f"Resend rejected email to {to_email}: {response.status_code} {response.text}")
