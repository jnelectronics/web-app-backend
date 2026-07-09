"""Generic CRUD used as a base by per-module repositories (SAD §5.2 layer responsibilities)."""

from typing import Generic, TypeVar

from sqlalchemy.orm import Session

from app.db.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    def __init__(self, db: Session, model: type[ModelType]) -> None:
        self.db = db
        self.model = model

    def get(self, id) -> ModelType | None:
        return self.db.get(self.model, id)

    def list(self, *, offset: int = 0, limit: int = 20) -> list[ModelType]:
        return self.db.query(self.model).offset(offset).limit(limit).all()

    def count(self) -> int:
        return self.db.query(self.model).count()

    def create(self, obj: ModelType) -> ModelType:
        self.db.add(obj)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def save(self, obj: ModelType) -> ModelType:
        self.db.commit()
        self.db.refresh(obj)
        return obj
