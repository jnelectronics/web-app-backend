import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from observability import setup_observability
from rate_limit import RateLimitedError
from routers import audit, auth, branches, cart, categories, customers, dashboard, inventory, orders, payments, products, promotions, staff, variants
from routers.inventory import InsufficientInventoryError
from routers.orders import InvalidStateTransitionError
from routers.payments import DuplicatePaymentError, PaymentsUnavailableError

load_dotenv()

# Logging + Sentry - see observability.py's docstring for why this is
# shared with worker.py rather than configured only here.
#
# Deliberately does NOT capture our own business-rule exceptions
# (InsufficientInventoryError, DuplicatePaymentError, etc.) as Sentry
# errors - those are already caught by the @app.exception_handler(...)
# calls below and turned into clean, expected responses (409/503/etc.)
# before Sentry's error-capturing middleware would ever see them as
# "unhandled." That's intentional: Sentry should surface genuine bugs, not
# expected business outcomes like "cart had insufficient stock."
setup_observability()

app = FastAPI()

# Without this, a browser-based frontend (the React storefront/dashboard)
# calling this API from a different origin gets silently blocked by the
# BROWSER itself before the request even reaches a route - the API
# response would be fine, but JavaScript never gets to see it.
# CORS_ALLOWED_ORIGINS is a comma-separated list in .env - defaults cover
# common local dev ports (Vite, Create React App) so this works out of the
# box before a real frontend domain exists. Add the real deployed frontend
# URL(s) here before this project goes anywhere near real users.
_cors_origins = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in _cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Maps an HTTP status code to the error_code the docs specify (§6) for the
# error envelope below. Anything not in this table (an unexpected 500,
# mainly) falls back to INTERNAL_ERROR.
_ERROR_CODES = {
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
}


# app.exception_handler intercepts a specific exception type raised ANYWHERE
# in a route, before FastAPI would otherwise turn it into a raw 500 error.
# This is what turns our plain-Python InsufficientInventoryError into a
# proper HTTP response, without every route that might raise it needing to
# catch it individually.
@app.exception_handler(InsufficientInventoryError)
def insufficient_inventory_handler(request: Request, exc: InsufficientInventoryError):
    return JSONResponse(
        status_code=409,
        content={"success": False, "message": str(exc), "error_code": "INSUFFICIENT_INVENTORY"},
    )


# Same pattern as above, for the payments-specific business rule (BR-PAY-005:
# an order can never have two successful payments).
@app.exception_handler(DuplicatePaymentError)
def duplicate_payment_handler(request: Request, exc: DuplicatePaymentError):
    return JSONResponse(
        status_code=409,
        content={"success": False, "message": str(exc), "error_code": "DUPLICATE_PAYMENT"},
    )


# Same pattern again, for when PesaPal isn't configured yet (e.g. still
# going through business verification) - a clean 503 with a friendly
# message, instead of every checkout attempt failing deep inside a raw
# PesaPal HTTP error.
@app.exception_handler(PaymentsUnavailableError)
def payments_unavailable_handler(request: Request, exc: PaymentsUnavailableError):
    return JSONResponse(
        status_code=503,
        content={"success": False, "message": str(exc), "error_code": "PAYMENTS_UNAVAILABLE"},
    )


# Same pattern again, for repeated failed login attempts (rate_limit.py) -
# 429 is the standard HTTP status for "too many requests," and the
# Retry-After header (not just the message body) is the standard way to
# tell a well-behaved client exactly how long to back off.
@app.exception_handler(RateLimitedError)
def rate_limited_handler(request: Request, exc: RateLimitedError):
    return JSONResponse(
        status_code=429,
        content={"success": False, "message": str(exc), "error_code": "RATE_LIMITED"},
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


# Same pattern again, for the order status lifecycle rule (FR-ORDER-012:
# an order can only move to specific next statuses from its current one).
@app.exception_handler(InvalidStateTransitionError)
def invalid_state_transition_handler(request: Request, exc: InvalidStateTransitionError):
    return JSONResponse(
        status_code=409,
        content={"success": False, "message": str(exc), "error_code": "INVALID_STATE_TRANSITION"},
    )


# Catches every HTTPException raised anywhere (all the plain
# `raise HTTPException(status_code=404, detail=...)` calls scattered across
# the routers) and reshapes FastAPI's default {"detail": ...} body into the
# error envelope the docs specify (§2.4), instead of changing every one of
# those raise sites individually.
@app.exception_handler(StarletteHTTPException)
def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": exc.detail if isinstance(exc.detail, str) else "Request failed.",
            "error_code": _ERROR_CODES.get(exc.status_code, "INTERNAL_ERROR"),
        },
    )


# Catches Pydantic/FastAPI's own request-validation failures (a missing
# required field, a string where a UUID was expected, etc.) - these
# normally bypass HTTPException entirely, so they need their own handler to
# land in the same error envelope shape instead of FastAPI's default
# {"detail": [...]} array.
#
# jsonable_encoder (not raw exc.errors()) - a custom Pydantic validator
# that raises a plain ValueError (see schemas.py's PasswordStr) makes
# Pydantic embed the actual ValueError OBJECT inside exc.errors()'s
# "ctx.error" field. json.dumps() can't serialize a raw exception object,
# and JSONResponse built from a plain dict like this doesn't run its
# content through FastAPI's encoder automatically - jsonable_encoder does
# that conversion explicitly (same helper FastAPI's own default handler
# uses internally for exactly this reason).
@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": "Validation failed.",
            "error_code": "VALIDATION_ERROR",
            "errors": jsonable_encoder(exc.errors()),
        },
    )


# Wires all the routes defined in each router into this app.
# prefix="/api/v1" is added ON TOP of each router's own prefix, giving paths
# like /api/v1/products and /api/v1/categories. Kept here (not in the router
# files) because API versioning is an app-wide decision, not a
# products/categories-specific one - every future domain gets included the
# same way, without needing to know about versioning itself.
app.include_router(products.router, prefix="/api/v1")
app.include_router(categories.router, prefix="/api/v1")
app.include_router(branches.router, prefix="/api/v1")
app.include_router(variants.router, prefix="/api/v1")
app.include_router(inventory.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(cart.router, prefix="/api/v1")
app.include_router(orders.router, prefix="/api/v1")
app.include_router(staff.router, prefix="/api/v1")
# webhook_router MUST be included BEFORE payments.router - Starlette
# matches routes in registration order, and payments.router's
# GET /payments/{payment_id} would otherwise match /payments/webhook
# first (payment_id="webhook" as a raw string - FastAPI only validates
# it's a real UUID AFTER routing has already picked this route), sending
# every webhook call into read_payment's auth check instead of the
# actual webhook handler.
#
# No route_class=EnvelopeRoute on webhook_router - it's PesaPal's IPN
# callback, not a client-facing endpoint, and PesaPal expects its own
# exact response shape (see routers/payments.py's payment_webhook for why).
app.include_router(payments.webhook_router, prefix="/api/v1")
app.include_router(payments.router, prefix="/api/v1")
app.include_router(customers.router, prefix="/api/v1")
app.include_router(promotions.banner_router, prefix="/api/v1")
app.include_router(promotions.discount_router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")


@app.get("/")
def read_root():
    return {"message": "JN Electronics API is alive"}
