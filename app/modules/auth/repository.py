import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.enums import OwnerType
from app.modules.auth.models import RefreshToken


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


class RefreshTokenRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def store(
        self, *, owner_type: OwnerType, owner_id: uuid.UUID, raw_token: str, expires_at: datetime
    ) -> RefreshToken:
        token = RefreshToken(
            owner_type=owner_type,
            owner_id=owner_id,
            token_hash=_hash_token(raw_token),
            expires_at=expires_at,
        )
        self.db.add(token)
        self.db.commit()
        self.db.refresh(token)
        return token

    def get_active(self, raw_token: str) -> RefreshToken | None:
        token_hash = _hash_token(raw_token)
        return (
            self.db.query(RefreshToken)
            .filter(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > datetime.now(timezone.utc),
            )
            .first()
        )

    def revoke(self, token: RefreshToken) -> None:
        token.revoked_at = datetime.now(timezone.utc)
        self.db.commit()
