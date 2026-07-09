from sqlalchemy.orm import Session

from app.db.repository import BaseRepository
from app.modules.products.models import Product, ProductVariant, VariantAttribute


class ProductRepository(BaseRepository[Product]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Product)


class ProductVariantRepository(BaseRepository[ProductVariant]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, ProductVariant)

    def add_attribute(self, variant_id, name: str, value: str) -> VariantAttribute:
        attribute = VariantAttribute(variant_id=variant_id, attribute_name=name, attribute_value=value)
        self.db.add(attribute)
        self.db.commit()
        return attribute
