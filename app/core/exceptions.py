"""Typed business exceptions, mapped to the standard error envelope (API Spec §2.4, §6)."""

from fastapi import Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    error_code: str = "INTERNAL_ERROR"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str, details: dict | None = None) -> None:
        self.message = message
        self.details = details
        super().__init__(message)


class ValidationError(AppError):
    error_code = "VALIDATION_ERROR"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


class UnauthorizedError(AppError):
    error_code = "UNAUTHORIZED"
    status_code = status.HTTP_401_UNAUTHORIZED


class ForbiddenError(AppError):
    error_code = "FORBIDDEN"
    status_code = status.HTTP_403_FORBIDDEN


class NotFoundError(AppError):
    error_code = "NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    error_code = "CONFLICT"
    status_code = status.HTTP_409_CONFLICT


class InsufficientInventoryError(AppError):
    """Requested quantity exceeds available stock (FR-INV-006)."""

    error_code = "INSUFFICIENT_INVENTORY"
    status_code = status.HTTP_409_CONFLICT


class InvalidStateTransitionError(AppError):
    """Order/payment status change not allowed from the current state."""

    error_code = "INVALID_STATE_TRANSITION"
    status_code = status.HTTP_409_CONFLICT


class DuplicatePaymentError(AppError):
    """A successful payment already exists for this order (BR-PAY-005)."""

    error_code = "DUPLICATE_PAYMENT"
    status_code = status.HTTP_409_CONFLICT


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": exc.message,
            "error_code": exc.error_code,
            **({"details": exc.details} if exc.details else {}),
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "message": "An unexpected error occurred.",
            "error_code": "INTERNAL_ERROR",
        },
    )
