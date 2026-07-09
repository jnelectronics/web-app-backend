from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_db_session, require_roles
from app.db.enums import StaffRole
from app.modules.promotions.schemas import (
    BannerCreate,
    BannerRead,
    ProductDiscountCreate,
    ProductDiscountRead,
)
from app.modules.promotions.service import PromotionService
from app.utils.responses import success_envelope

router = APIRouter(prefix="/promotions", tags=["Promotions"])

_manage_roles = require_roles(StaffRole.INVENTORY_MANAGER.value, StaffRole.SYSTEM_ADMINISTRATOR.value)


@router.get("/banners")
def list_banners(db: Session = Depends(get_db_session)):
    banners = PromotionService(db).list_active_banners()
    return success_envelope([BannerRead.model_validate(b) for b in banners], "Banners retrieved successfully.")


@router.post("/banners", status_code=status.HTTP_201_CREATED)
def create_banner(
    payload: BannerCreate, principal=Depends(_manage_roles), db: Session = Depends(get_db_session)
):
    banner = PromotionService(db).create_banner(payload)
    return success_envelope(BannerRead.model_validate(banner), "Banner created successfully.")


@router.post("/discounts", status_code=status.HTTP_201_CREATED)
def create_discount(
    payload: ProductDiscountCreate, principal=Depends(_manage_roles), db: Session = Depends(get_db_session)
):
    discount = PromotionService(db).create_discount(payload)
    return success_envelope(ProductDiscountRead.model_validate(discount), "Discount created successfully.")


# TODO: PATCH/DELETE banners & discounts per API Specification §5.10.
