from sqlalchemy.orm import Session

from app.db.repository import BaseRepository
from app.modules.promotions.models import Banner, ProductDiscount


class BannerRepository(BaseRepository[Banner]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Banner)


class ProductDiscountRepository(BaseRepository[ProductDiscount]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, ProductDiscount)
