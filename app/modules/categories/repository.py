from sqlalchemy.orm import Session

from app.db.repository import BaseRepository
from app.modules.categories.models import Category


class CategoryRepository(BaseRepository[Category]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Category)
