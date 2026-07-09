"""Helpers for the standard success envelope (API Spec §2.3)."""

from typing import Any


def success_envelope(data: Any, message: str = "Operation completed successfully.") -> dict:
    return {"success": True, "message": message, "data": data}


def paginated_envelope(
    data: list,
    *,
    page: int,
    page_size: int,
    total_records: int,
    message: str = "Records retrieved successfully.",
) -> dict:
    total_pages = (total_records + page_size - 1) // page_size if page_size else 0
    return {
        "success": True,
        "message": message,
        "data": data,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_records": total_records,
            "total_pages": total_pages,
        },
    }
