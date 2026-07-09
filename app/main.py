from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.exceptions import AppError, app_error_handler, unhandled_exception_handler
from app.core.logging import configure_logging
from app.middleware.request_logging import RequestLoggingMiddleware
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.branches.router import router as branches_router
from app.modules.cart.router import router as cart_router
from app.modules.categories.router import router as categories_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.inventory.router import router as inventory_router
from app.modules.orders.router import router as orders_router
from app.modules.payments.router import router as payments_router
from app.modules.products.router import router as products_router
from app.modules.promotions.router import router as promotions_router
from app.modules.users.router import customers_router, staff_router
from app.utils.responses import success_envelope

configure_logging()

app = FastAPI(title=settings.PROJECT_NAME, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

for router in (
    auth_router,
    customers_router,
    staff_router,
    categories_router,
    products_router,
    branches_router,
    inventory_router,
    cart_router,
    orders_router,
    payments_router,
    promotions_router,
    dashboard_router,
    audit_router,
):
    app.include_router(router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["Health"])
def health_check():
    return success_envelope({"status": "ok", "environment": settings.ENVIRONMENT}, "Service is healthy.")
