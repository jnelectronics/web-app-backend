# All the HTTP routes for the "categories" domain live here - both plain
# categories, and (new) the category GROUPS they belong to. Two routers in
# one file, same reasoning as routers/promotions.py's banner_router +
# discount_router: closely related domains, different URL prefixes.

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Category, CategoryGroup, StaffRole, StaffUser
from pagination import build_pagination_meta
from schemas import (
    CategoryCreate,
    CategoryGroupCreate,
    CategoryGroupRead,
    CategoryGroupStatusUpdate,
    CategoryGroupWithCategories,
    CategoryRead,
    PaginatedResponse,
)
from security import decode_token_claims, require_staff_role

router = APIRouter(prefix="/categories", tags=["categories"], route_class=EnvelopeRoute)
group_router = APIRouter(prefix="/category-groups", tags=["categories"], route_class=EnvelopeRoute)

# Same "optional auth" trick routers/promotions.py's banner include_inactive
# view uses - the public storefront call needs no token at all, but
# include_inactive=true is an admin view that DOES need one, so the route
# itself can't just require a bearer token unconditionally.
_optional_bearer_scheme = HTTPBearer(auto_error=False)


# Both of these are plain Python exceptions (not raised as raw
# HTTPException inline) - registered in main.py via @app.exception_handler,
# same pattern as InsufficientInventoryError/DuplicatePaymentError/etc.
# Keeps the HTTP-status/error_code mapping for these two business rules in
# one central place instead of repeated inline in every route that could
# hit them.
class DuplicateHeaderRankError(Exception):
    def __init__(self, header_rank: int):
        self.header_rank = header_rank
        super().__init__(f"header_rank {header_rank} is already taken by another category group")


class CategoryGroupHasCategoriesError(Exception):
    def __init__(self):
        super().__init__("Cannot delete a category group that still has categories assigned to it")


def _assert_header_rank_free(db: Session, header_rank: int | None, *, exclude_group_id: uuid.UUID | None = None):
    # A pre-check, not a try/except around the INSERT - simpler to read than
    # catching the IntegrityError the model's own unique constraint would
    # raise, and gives a clean, specific error message before Postgres ever
    # sees the bad value. exclude_group_id lets an UPDATE keep its OWN
    # existing rank without tripping over itself.
    if header_rank is None:
        return
    query = db.query(CategoryGroup).filter(CategoryGroup.header_rank == header_rank)
    if exclude_group_id is not None:
        query = query.filter(CategoryGroup.id != exclude_group_id)
    if query.first() is not None:
        raise DuplicateHeaderRankError(header_rank)


def _group_with_categories(db: Session, group: CategoryGroup, include_inactive_categories: bool) -> CategoryGroupWithCategories:
    # Builds the nested {"...group fields..., "categories": [...]} shape
    # Nyson's frontend asked for, so it doesn't have to make a second
    # request per group just to render a header bar / nav menu.
    query = db.query(Category).filter(Category.category_group_id == group.id)
    if not include_inactive_categories:
        query = query.filter(Category.is_active == True)  # noqa: E712
    categories = query.all()
    return CategoryGroupWithCategories(
        id=group.id,
        name=group.name,
        icon=group.icon,
        header_rank=group.header_rank,
        display_order=group.display_order,
        is_active=group.is_active,
        created_at=group.created_at,
        updated_at=group.updated_at,
        categories=categories,
    )


@group_router.get("", response_model=list[CategoryGroupWithCategories])
def list_category_groups(
    include_inactive: bool = False,
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer_scheme),
    db: Session = Depends(get_db),
):
    if not include_inactive:
        # Public storefront view: only active groups, and only their
        # active categories nested inside.
        groups = db.query(CategoryGroup).filter(CategoryGroup.is_active == True).order_by(CategoryGroup.display_order).all()  # noqa: E712
        return [_group_with_categories(db, g, include_inactive_categories=False) for g in groups]

    # include_inactive=true is the admin management view - same manual
    # staff-token check as banners' include_inactive, since making auth
    # unconditional here would break the public branch above.
    invalid_token = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
    if credentials is None:
        raise invalid_token
    claims = decode_token_claims(credentials.credentials)
    if claims is None or claims.get("type") != "staff":
        raise invalid_token
    staff = db.get(StaffUser, uuid.UUID(claims["sub"]))
    if staff is None or not staff.is_active:
        raise invalid_token
    if staff.role not in (StaffRole.OWNER, StaffRole.SALES_ATTENDANT, StaffRole.SYSTEM_ADMINISTRATOR):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted")

    groups = db.query(CategoryGroup).order_by(CategoryGroup.display_order).all()
    return [_group_with_categories(db, g, include_inactive_categories=True) for g in groups]


@group_router.get("/{group_id}", response_model=CategoryGroupWithCategories)
def read_category_group(group_id: uuid.UUID, db: Session = Depends(get_db)):
    group = db.get(CategoryGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Category group not found")
    return _group_with_categories(db, group, include_inactive_categories=True)


@group_router.post("", response_model=CategoryGroupRead)
def create_category_group(
    group: CategoryGroupCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    _assert_header_rank_free(db, group.header_rank)
    new_group = CategoryGroup(
        name=group.name,
        icon=group.icon,
        header_rank=group.header_rank,
        display_order=group.display_order,
    )
    db.add(new_group)
    db.commit()
    db.refresh(new_group)
    return new_group


@group_router.put("/{group_id}", response_model=CategoryGroupRead)
def update_category_group(
    group_id: uuid.UUID,
    group: CategoryGroupCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    existing = db.get(CategoryGroup, group_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Category group not found")

    _assert_header_rank_free(db, group.header_rank, exclude_group_id=group_id)
    existing.name = group.name
    existing.icon = group.icon
    existing.header_rank = group.header_rank
    existing.display_order = group.display_order
    db.commit()
    db.refresh(existing)
    return existing


@group_router.patch("/{group_id}/status", response_model=CategoryGroupRead)
def set_category_group_status(
    group_id: uuid.UUID,
    update: CategoryGroupStatusUpdate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    # Sets rather than toggles - same reasoning as routers/staff.py's
    # set_staff_status.
    existing = db.get(CategoryGroup, group_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Category group not found")

    existing.is_active = update.is_active
    db.commit()
    db.refresh(existing)
    return existing


@group_router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category_group(
    group_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    # A REAL delete, not soft - unlike Category/Product below. Only allowed
    # when nothing still points at this group, since every category is
    # REQUIRED to have a category_group_id; letting a group with categories
    # under it disappear would leave those categories pointing at nothing.
    existing = db.get(CategoryGroup, group_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Category group not found")

    has_categories = db.query(Category).filter(Category.category_group_id == group_id).first() is not None
    if has_categories:
        raise CategoryGroupHasCategoriesError()

    db.delete(existing)
    db.commit()


@router.get("/{category_id}", response_model=CategoryRead)
def read_category(category_id: uuid.UUID, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


@router.get("", response_model=PaginatedResponse[CategoryRead])
def list_categories(
    skip: int = 0,
    limit: int = 10,
    category_group_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Category).filter(Category.is_active == True)  # noqa: E712
    if category_group_id is not None:
        query = query.filter(Category.category_group_id == category_group_id)
    total = query.count()
    categories = query.offset(skip).limit(limit).all()
    return PaginatedResponse[CategoryRead](
        items=categories, pagination=build_pagination_meta(skip, limit, total)
    )


@router.post("", response_model=CategoryRead)
def create_category(
    category: CategoryCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    if db.get(CategoryGroup, category.category_group_id) is None:
        raise HTTPException(status_code=404, detail="Category group not found")

    new_category = Category(
        name=category.name,
        description=category.description,
        category_group_id=category.category_group_id,
    )
    db.add(new_category)
    db.commit()
    db.refresh(new_category)
    return new_category


@router.put("/{category_id}", response_model=CategoryRead)
def update_category(
    category_id: uuid.UUID,
    category: CategoryCreate,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    existing = db.get(Category, category_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Category not found")

    if db.get(CategoryGroup, category.category_group_id) is None:
        raise HTTPException(status_code=404, detail="Category group not found")

    existing.name = category.name
    existing.description = category.description
    existing.category_group_id = category.category_group_id
    db.commit()
    db.refresh(existing)
    return existing


# Soft-delete, same reasoning as products: never actually remove the row.
@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: uuid.UUID,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.OWNER, StaffRole.SALES_ATTENDANT)),
    db: Session = Depends(get_db),
):
    existing = db.get(Category, category_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Category not found")

    existing.is_active = False
    db.commit()
