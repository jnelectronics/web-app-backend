# Wraps PesaPal API 3.0 - the real payment gateway now backing
# routers/payments.py. Every PesaPal-specific detail (auth token caching,
# request/response shapes) lives here, so the router only ever deals with
# plain Python values, not PesaPal's JSON directly. Same idea as
# database.py wrapping SQLAlchemy, or security.py wrapping JWT.
#
# Uses httpx (already a dependency, for TestClient) rather than adding
# `requests` as a new one - httpx.Client() does the same synchronous
# request job.

import os
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

PESAPAL_CONSUMER_KEY = os.getenv("PESAPAL_CONSUMER_KEY")
PESAPAL_CONSUMER_SECRET = os.getenv("PESAPAL_CONSUMER_SECRET")
# Sandbox by default - switching to the live base URL is a deliberate,
# separate step for when this project actually goes live with real money,
# not something that should happen by accident.
PESAPAL_BASE_URL = os.getenv("PESAPAL_BASE_URL", "https://cybqa.pesapal.com/pesapalv3")
# Set once by register_pesapal_ipn.py (run manually, like seed_admin.py) -
# PesaPal wants you to register your webhook URL ONCE and reuse the ID it
# hands back, not tell it a URL on every single payment.
PESAPAL_IPN_ID = os.getenv("PESAPAL_IPN_ID")
# Where the CUSTOMER'S BROWSER lands after paying - this should be a page
# on the real storefront frontend, not this API. Defaults to a placeholder
# so local testing doesn't crash, but must be a real frontend URL before
# this project goes anywhere near real users.
PESAPAL_CALLBACK_URL = os.getenv("PESAPAL_CALLBACK_URL", "http://localhost:3000/order-confirmation")


def is_configured() -> bool:
    # True once real credentials exist in .env - False (the default, out
    # of the box) while waiting on PesaPal's business verification, which
    # can take a while for a real merchant account. routers/payments.py
    # checks this BEFORE attempting a real call, so customers see a clean
    # "coming soon" message instead of every checkout attempt failing deep
    # inside a PesaPal HTTP error. Nothing else needs to change once real
    # keys are added to .env - payments turn themselves back on.
    return bool(PESAPAL_CONSUMER_KEY) and bool(PESAPAL_CONSUMER_SECRET)


class PesaPalError(Exception):
    # Raised whenever PesaPal's response doesn't have the field we asked
    # for - wraps their raw error body so it's visible in logs/tracebacks
    # without every call site needing to know PesaPal's error JSON shape.
    pass


# Cached at module level (not per-request) - PesaPal's access token is
# valid for 5 minutes (see docs), so re-fetching it on every single API
# call would be both slow and unnecessary.
_cached_token: str | None = None
_cached_token_expires_at: datetime | None = None


def _get_access_token() -> str:
    global _cached_token, _cached_token_expires_at

    # 30-second safety margin - refresh slightly early rather than start a
    # request with a token that might expire mid-flight.
    if (
        _cached_token is not None
        and _cached_token_expires_at is not None
        and datetime.now(timezone.utc) < _cached_token_expires_at - timedelta(seconds=30)
    ):
        return _cached_token

    response = httpx.post(
        f"{PESAPAL_BASE_URL}/api/Auth/RequestToken",
        json={"consumer_key": PESAPAL_CONSUMER_KEY, "consumer_secret": PESAPAL_CONSUMER_SECRET},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=15,
    )
    data = response.json()
    if "token" not in data:
        raise PesaPalError(f"Failed to get PesaPal access token: {data}")

    _cached_token = data["token"]
    _cached_token_expires_at = datetime.fromisoformat(data["expiryDate"].replace("Z", "+00:00"))
    return _cached_token


def register_ipn(url: str, notification_type: str = "GET") -> str:
    # Only ever called by register_pesapal_ipn.py, the one-off setup
    # script - not called during normal request handling.
    token = _get_access_token()
    response = httpx.post(
        f"{PESAPAL_BASE_URL}/api/URLSetup/RegisterIPN",
        json={"url": url, "ipn_notification_type": notification_type},
        headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        timeout=15,
    )
    data = response.json()
    if "ipn_id" not in data:
        raise PesaPalError(f"RegisterIPN failed: {data}")
    return data["ipn_id"]


def submit_order_request(
    merchant_reference: str,
    amount: float,
    currency: str,
    description: str,
    billing_email: str | None,
    billing_phone: str,
    billing_first_name: str,
    billing_last_name: str,
) -> dict:
    # Returns {"order_tracking_id": ..., "redirect_url": ...} - order_tracking_id
    # is what we store as Payment.provider_reference; redirect_url is
    # PesaPal's hosted checkout page the customer's browser needs to visit.
    token = _get_access_token()
    response = httpx.post(
        f"{PESAPAL_BASE_URL}/api/Transactions/SubmitOrderRequest",
        json={
            "id": merchant_reference,
            "currency": currency,
            "amount": amount,
            "description": description,
            "callback_url": PESAPAL_CALLBACK_URL,
            "notification_id": PESAPAL_IPN_ID,
            "billing_address": {
                "email_address": billing_email,
                "phone_number": billing_phone,
                "first_name": billing_first_name,
                "last_name": billing_last_name,
            },
        },
        headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        timeout=15,
    )
    data = response.json()
    if "order_tracking_id" not in data:
        raise PesaPalError(f"SubmitOrderRequest failed: {data}")

    return {"order_tracking_id": data["order_tracking_id"], "redirect_url": data["redirect_url"]}


def get_transaction_status(order_tracking_id: str) -> dict:
    # Returns PesaPal's raw response - callers care mostly about
    # payment_status_description ("COMPLETED"/"FAILED"/"INVALID"/"REVERSED").
    # This is the ONLY way to learn a payment's real status - the IPN
    # callback that triggers this call deliberately doesn't carry it.
    token = _get_access_token()
    response = httpx.get(
        f"{PESAPAL_BASE_URL}/api/Transactions/GetTransactionStatus",
        params={"orderTrackingId": order_tracking_id},
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        timeout=15,
    )
    data = response.json()
    if "payment_status_description" not in data:
        raise PesaPalError(f"GetTransactionStatus failed: {data}")

    return data
