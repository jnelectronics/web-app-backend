"""Shared FastAPI dependencies: DB session, authenticated principal, role enforcement."""

from collections.abc import Generator

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.db.session import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


class CurrentPrincipal:
    def __init__(self, id: str, role: str) -> None:
        self.id = id
        self.role = role


def get_current_principal(token: str | None = Depends(oauth2_scheme)) -> CurrentPrincipal:
    if not token:
        raise UnauthorizedError("Authentication required.")
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise UnauthorizedError("Invalid or expired token.") from exc
    if payload.get("type") != "access":
        raise UnauthorizedError("Invalid or expired token.")
    return CurrentPrincipal(id=payload["sub"], role=payload["role"])


def require_roles(*allowed_roles: str):
    def _checker(principal: CurrentPrincipal = Depends(get_current_principal)) -> CurrentPrincipal:
        if principal.role not in allowed_roles:
            raise ForbiddenError("You do not have permission to access this resource.")
        return principal

    return _checker


def get_db_session() -> Generator[Session, None, None]:
    yield from get_db()
