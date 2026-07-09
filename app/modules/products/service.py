import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.modules.products.models import Product, ProductVariant
from app.modules.products.repository import ProductRepository, ProductVariantRepository
from app.modules.products.schemas import (
    ProductCreate,
    ProductStatusUpdate,
    ProductUpdate,
    ProductVariantCreate,
)


class ProductService:
    def __init__(self, db: Session) -> None:
        self.repository = ProductRepository(db)
        self.variants = ProductVariantRepository(db)

    def list_products(self, *, page: int, page_size: int) -> tuple[list[Product], int]:
        offset = (page - 1) * page_size
        return self.repository.list(offset=offset, limit=page_size), self.repository.count()

    def get_product(self, product_id: uuid.UUID) -> Product:
        product = self.repository.get(product_id)
        if not product:
            raise NotFoundError("Product not found.")
        return product

    def create_product(self, payload: ProductCreate) -> Product:
        return self.repository.create(Product(**payload.model_dump()))

    def update_product(self, product_id: uuid.UUID, payload: ProductUpdate) -> Product:
        product = self.get_product(product_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(product, field, value)
        return self.repository.save(product)

    def set_status(self, product_id: uuid.UUID, payload: ProductStatusUpdate) -> Product:
        product = self.get_product(product_id)
        product.is_active = payload.is_active
        return self.repository.save(product)

    def add_variant(self, product_id: uuid.UUID, payload: ProductVariantCreate) -> ProductVariant:
        self.get_product(product_id)
        variant = self.variants.create(
            ProductVariant(
                product_id=product_id,
                sku=payload.sku,
                variant_label=payload.variant_label,
                price=payload.price,
            )
        )
        for name, value in payload.attributes.items():
            self.variants.add_attribute(variant.id, name, value)
        self.variants.db.refresh(variant)
        return variant

    # TODO: PATCH variant, variant status, product images (add/replace/delete/set-primary)
    # per API Specification §5.4 — follow the same repository/service pattern above.
