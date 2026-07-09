from sqlalchemy.orm import Session

from app.modules.promotions.models import Banner, ProductDiscount
from app.modules.promotions.repository import BannerRepository, ProductDiscountRepository
from app.modules.promotions.schemas import BannerCreate, ProductDiscountCreate


class PromotionService:
    def __init__(self, db: Session) -> None:
        self.banners = BannerRepository(db)
        self.discounts = ProductDiscountRepository(db)

    def list_active_banners(self) -> list[Banner]:
        return self.banners.db.query(Banner).filter(Banner.is_active.is_(True)).all()

    def create_banner(self, payload: BannerCreate) -> Banner:
        return self.banners.create(Banner(**payload.model_dump()))

    def create_discount(self, payload: ProductDiscountCreate) -> ProductDiscount:
        return self.discounts.create(ProductDiscount(**payload.model_dump()))
