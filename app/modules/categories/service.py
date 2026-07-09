import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.modules.categories.models import Category
from app.modules.categories.repository import CategoryRepository
from app.modules.categories.schemas import CategoryCreate, CategoryStatusUpdate, CategoryUpdate


class CategoryService:
    def __init__(self, db: Session) -> None:
        self.repository = CategoryRepository(db)

    def list_categories(self, *, page: int, page_size: int) -> tuple[list[Category], int]:
        offset = (page - 1) * page_size
        return self.repository.list(offset=offset, limit=page_size), self.repository.count()

    def get_category(self, category_id: uuid.UUID) -> Category:
        category = self.repository.get(category_id)
        if not category:
            raise NotFoundError("Category not found.")
        return category

    def create_category(self, payload: CategoryCreate) -> Category:
        return self.repository.create(Category(**payload.model_dump()))

    def update_category(self, category_id: uuid.UUID, payload: CategoryUpdate) -> Category:
        category = self.get_category(category_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(category, field, value)
        return self.repository.save(category)

    def set_status(self, category_id: uuid.UUID, payload: CategoryStatusUpdate) -> Category:
        category = self.get_category(category_id)
        category.is_active = payload.is_active
        return self.repository.save(category)
