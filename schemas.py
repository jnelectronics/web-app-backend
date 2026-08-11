# This file's job: define the JSON "shapes" the API accepts and returns.
# One pair per domain - a *Create schema (what a client sends us) and a
# *Read schema (what we send back).

import enum
import re
import uuid
from datetime import datetime
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, computed_field

from models import (
    CatalogueFilterMode,
    CustomerStatus,
    CustomerType,
    DiscountType,
    HomepageSectionType,
    MovementType,
    OrderStatus,
    PaymentStatus,
    StaffRole,
)

# Generic wrapper for every collection response - see pagination.py for the
# actual page/total_pages math. `T` stands in for whatever *Read schema a
# given list endpoint returns (ProductRead, OrderRead, etc.) - PaginatedResponse[ProductRead]
# reads as "a page of products", the same idea as list[ProductRead] but with
# a pagination block alongside the items instead of just a bare array.
T = TypeVar("T")


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_records: int
    total_pages: int


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    pagination: PaginationMeta


def _validate_password_strength(password: str) -> str:
    # Deliberately modest requirements for a pilot - long enough and mixed
    # enough to rule out trivial passwords ("password", "12345678"), not
    # so strict it becomes user-hostile. AfterValidator runs this AFTER
    # Pydantic's own type check, so `password` here is already guaranteed
    # to be a str.
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not re.search(r"[A-Za-z]", password):
        raise ValueError("Password must contain at least one letter")
    if not re.search(r"[0-9]", password):
        raise ValueError("Password must contain at least one digit")
    return password


# A reusable annotated type - anywhere a NEW password is being set (not
# `current_password` fields, which check an existing password against its
# hash and shouldn't reject a login/verification just because a password
# set before this rule existed happens to be weak) uses this instead of
# plain `str`, so the rule lives in exactly one place.
PasswordStr = Annotated[str, AfterValidator(_validate_password_strength)]


class CategoryGroupCreate(BaseModel):
    name: str
    icon: str | None = None
    # 1-5, or None to leave this group out of the desktop header bar
    # entirely. The model's own CheckConstraint is the REAL enforcement of
    # the 1-5 range (Postgres will reject anything else no matter what);
    # this is just here so a bad value fails fast, before a query even
    # reaches the database.
    header_rank: int | None = None
    display_order: int = 0


class CategoryGroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    icon: str | None
    header_rank: int | None
    display_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CategoryGroupStatusUpdate(BaseModel):
    # Same "sets, not toggles" reasoning as StaffStatusUpdate above.
    is_active: bool


class CategoryGroupWithCategories(CategoryGroupRead):
    # The storefront view (GET /category-groups) embeds each group's own
    # categories directly in the same response, so the frontend doesn't
    # have to make a second request per group just to render a header
    # bar / nav menu - see routers/categories.py.
    categories: list["CategoryRead"]


class CategoryCreate(BaseModel):
    name: str
    description: str | None = None
    category_group_id: uuid.UUID


class CategoryRead(BaseModel):
    # Lets Pydantic build this straight from a SQLAlchemy Category object's
    # attributes (e.g. category.name), not just from a plain dict.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    category_group_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime


class BranchCreate(BaseModel):
    name: str
    address: str
    phone_number: str | None = None
    email: str | None = None


class BranchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    address: str
    phone_number: str | None
    email: str | None
    is_active: bool
    # Which branch the branch-less product-form "quantity" field reads/
    # writes against - see routers/variants.py's stock endpoint and
    # routers/branches.py's set_default_branch.
    is_default: bool
    created_at: datetime
    updated_at: datetime


class ProductCreate(BaseModel):
    # The client must say which category this product belongs to -
    # matches the NOT NULL foreign key on the real products table.
    category_id: uuid.UUID
    name: str
    # max_length=2000 matches models.py's Product.description column
    # (String(2000)) exactly. Without this, Pydantic would accept a
    # description of any length and pass it straight through to Postgres -
    # which then rejects anything over 2000 characters with a raw database
    # error. That error isn't one of the custom exceptions this project
    # catches (see main.py's @app.exception_handler list), so FastAPI's
    # default handler takes over instead: a bare-text "Internal Server
    # Error", not our usual {"success": false, ...} JSON envelope. Setting
    # the limit here means Pydantic catches it first and returns a normal,
    # clean 422 through the SAME validation-error handling every other bad
    # request already goes through - one boundary, not two.
    description: str | None = Field(default=None, max_length=2000)
    is_featured: bool = False
    is_new_arrival: bool = False
    # is_on_sale=True is only accepted when the product already has an
    # active promotion applied (routers/products.py's
    # _assert_on_sale_has_promotion) - which for a brand-new product (this
    # schema, on POST) is never true yet, since apply-promotion needs a
    # real product id to target. Practically: create with this False, then
    # POST /products/{id}/apply-promotion, which flips it on for you.
    is_on_sale: bool = False


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category_id: uuid.UUID
    name: str
    description: str | None
    is_featured: bool
    is_new_arrival: bool
    is_on_sale: bool
    applied_promotion_id: uuid.UUID | None
    # NOT from_attributes-mapped off the Product row directly - both are
    # assembled by routers/products.py's _build_product_read (images come
    # from a separate table; is_discounted is computed from
    # product_discounts' current time window, never stored). ProductRead
    # still gets constructed explicitly there either way, same pattern
    # OrderRead already uses for its own `items` list.
    slug: str
    is_discounted: bool
    images: list["ProductImageRead"]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class VariantCreate(BaseModel):
    # The client must say which product this variant belongs to - matches
    # the NOT NULL foreign key on the real product_variants table.
    product_id: uuid.UUID
    sku: str
    variant_label: str | None = None
    price: float
    # e.g. {"color": "Black", "capacity": "128GB"} - one VariantAttribute
    # row gets written per key/value pair (see routers/variants.py). None
    # and {} both mean "no attributes", not "clear existing ones on update".
    attributes: dict[str, str] | None = None


class VariantRead(BaseModel):
    # Not from_attributes - a raw ProductVariant object has no "attributes"
    # python attribute (it's a separate EAV table, no ORM relationship
    # wiring in this project), so every route builds this manually via
    # routers/variants.py's _build_variant_read helper instead.
    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    variant_label: str | None
    price: float
    is_active: bool
    created_at: datetime
    updated_at: datetime
    attributes: dict[str, str]
    # Aggregate across EVERY branch's InventoryRecord for this variant -
    # deliberately just a boolean, exposing no actual quantities or which
    # branch(es) have stock (inventory levels are staff-only information).
    # No "low_stock" flag alongside this yet - that needs a threshold
    # (how few units counts as "low"?) that's a product decision, not
    # something to invent a number for here.
    in_stock: bool
    # The DEFAULT branch's actual quantity_available - unlike in_stock
    # above, this IS a real number, so it's only ever populated for a
    # staff-authenticated caller (routers/variants.py's read routes accept
    # an OPTIONAL staff token for exactly this reason). A public/customer
    # request always gets None here, never a real figure - per FR-PROD-017/018,
    # actual stock counts are staff-only information, same reasoning
    # in_stock is a plain boolean instead of a number.
    quantity_available: int | None = None


class VariantStockUpdate(BaseModel):
    # An ABSOLUTE quantity to set, not a +/- delta like InventoryAdjust -
    # matches how the admin product form actually works (staff type "10
    # in stock", not "add 3 more"). See routers/variants.py's
    # set_variant_stock for how this gets translated into a signed
    # InventoryMovement under the hood.
    quantity_available: int


# No ProductImageCreate here anymore - POST/PUT .../images now take a real
# uploaded file (multipart/form-data), not a JSON body, so routers/products.py
# declares `file: UploadFile` and `display_order: int = Form(0)` directly on
# the route function instead of via a Pydantic request-body model. Pydantic
# models describe JSON bodies; a file upload isn't one.


class ProductImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    image_url: str
    cloudinary_public_id: str | None
    is_primary: bool
    display_order: int
    created_at: datetime


# ProductRead references "ProductImageRead" as a forward-reference STRING
# (it's defined earlier in this file, before this class exists yet) -
# model_rebuild() tells Pydantic to resolve that string now that the real
# class is available, rather than leaving it unresolved.
ProductRead.model_rebuild()


class InventoryCreate(BaseModel):
    variant_id: uuid.UUID
    branch_id: uuid.UUID
    # Defaults to 0 - a stock record for a variant/branch pair usually
    # starts empty and gets stocked up afterwards via the adjust endpoint,
    # rather than being created with an arbitrary starting count.
    quantity_available: int = 0
    quantity_reserved: int = 0


class InventoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_id: uuid.UUID
    branch_id: uuid.UUID
    quantity_available: int
    quantity_reserved: int
    created_at: datetime
    updated_at: datetime


class CustomerRegister(BaseModel):
    # Note: no customer_type here - registering through this endpoint always
    # produces a `registered` customer. There's no client-facing way to
    # create a `guest` row; that only ever happens as a side effect
    # elsewhere (checkout without an account), not built yet.
    full_name: str
    email: str
    phone_number: str | None = None
    password: PasswordStr


class CustomerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_type: CustomerType
    full_name: str | None
    email: str | None
    phone_number: str | None
    status: CustomerStatus
    created_at: datetime
    updated_at: datetime


class CustomerProfileUpdate(BaseModel):
    full_name: str | None = None
    phone_number: str | None = None


class CustomerStatusUpdate(BaseModel):
    # Sets status to exactly this value - same "not a toggle" reasoning as
    # StaffStatusUpdate above, using this resource's own enum rather than
    # a plain bool since that's what Customer.status actually is.
    status: CustomerStatus


class CustomerPasswordChange(BaseModel):
    current_password: str
    new_password: PasswordStr


class CustomerLogin(BaseModel):
    # "identifier" (not "email") - matches the original written spec, and
    # lets a customer log in with either their email OR phone number
    # (added 2026-08-06 - previously email-only, even though registration
    # already collected an optional phone number).
    identifier: str
    password: str


class GoogleSignIn(BaseModel):
    # The raw ID token the FRONTEND gets back from Google's own JavaScript
    # library after the customer signs in with Google - google_auth_client.py
    # is what actually verifies this is genuine, not this schema. This
    # model's only job is describing the request body shape.
    id_token: str


class StaffLogin(BaseModel):
    email: str
    password: str


class Token(BaseModel):
    access_token: str
    # "bearer" is the standard scheme name meaning "just attach this token
    # as-is in an Authorization header" - not a value specific to this app.
    token_type: str = "bearer"


class TokenPair(BaseModel):
    # What /auth/login, /auth/staff/login, and /auth/refresh all return -
    # an access token for calling the API, plus a refresh token for getting
    # a new access token later without logging in again.
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class CustomerRegisterResponse(BaseModel):
    # Registration returns tokens immediately (per the docs' example), not
    # just the new customer record - so a client can go straight from
    # registering to being logged in, without a separate /auth/login call.
    customer: CustomerRead
    access_token: str
    refresh_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordForgotRequest(BaseModel):
    email: str


class PasswordResetRequest(BaseModel):
    token: str
    new_password: PasswordStr


class InventoryAdjust(BaseModel):
    # Signed delta applied to quantity_available - positive to stock in,
    # negative to take stock out (e.g. a sale). This is what the
    # InsufficientInventoryError rule checks: the record's current
    # quantity_available + this change can never go below zero.
    quantity_change: int
    # Both optional, defaulting to a generic manual adjustment - lets a
    # caller be more specific (e.g. movement_type="stock_in", reason="New
    # delivery from supplier") without breaking a caller that just sends
    # quantity_change like before this field existed.
    movement_type: MovementType = MovementType.ADJUSTMENT
    reason: str | None = None


class InventoryMovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    inventory_record_id: uuid.UUID
    movement_type: MovementType
    quantity_changed: int
    reason: str | None
    staff_user_id: uuid.UUID | None
    order_id: uuid.UUID | None
    created_at: datetime


class CartItemAdd(BaseModel):
    variant_id: uuid.UUID
    quantity: int


class CartItemUpdate(BaseModel):
    # Only quantity can change on an existing line - swapping variant_id
    # would just mean removing this item and adding a different one.
    quantity: int


class CartItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_id: uuid.UUID
    # NOT real columns on CartItem - routers/cart.py's _build_cart_read
    # builds these explicitly, by looking up the variant/product/primary
    # image fresh each time (not snapshotted the way unit_price_snapshot
    # is - a product's NAME/image changing later should show up on an
    # unpurchased cart line; only the price is deliberately frozen at
    # add-to-cart time). Nullable in case the underlying variant/product
    # somehow can't be found (soft-deleted rows still exist, so this
    # should be rare to never in practice, not a normal case).
    product_id: uuid.UUID | None
    product_name: str | None
    variant_label: str | None
    sku: str | None
    image_url: str | None
    quantity: int
    unit_price_snapshot: float

    # DISPLAY ONLY - resolved fresh at read time by routers/cart.py's
    # _resolve_discounted_price, so the frontend doesn't have to
    # reimplement percentage/fixed discount math itself (api-concerns.md
    # §10). original_price always equals unit_price_snapshot (the actual
    # price this line is billed at - see that field's own comment on why
    # it's frozen); discounted_price is None when no discount currently
    # applies, or the lower price if one does. Deliberately does NOT change
    # what line_total/subtotal below actually charge - this project's
    # checkout/PesaPal flow is verified live with real money, and isn't
    # touched by this addition.
    original_price: float
    discounted_price: float | None

    # Not a real database column - computed on the way out so a client
    # doesn't have to multiply quantity * unit_price_snapshot itself.
    # @computed_field is what makes this actually appear in the JSON
    # response - a plain @property would be invisible to serialization.
    @computed_field
    @property
    def line_total(self) -> float:
        return self.quantity * self.unit_price_snapshot


class CartRead(BaseModel):
    id: uuid.UUID
    items: list[CartItemRead]

    # Also computed, same reasoning as line_total - sums every line so the
    # client gets the cart total for free instead of re-deriving it.
    @computed_field
    @property
    def subtotal(self) -> float:
        return sum(item.line_total for item in self.items)


class CheckoutRequest(BaseModel):
    # Collected fresh at checkout regardless of who's ordering - matches
    # Order.guest_full_name etc. being NOT NULL even for registered
    # customers (see the model's comment for why).
    guest_full_name: str
    guest_phone_number: str
    guest_email: str | None = None
    delivery_address: str
    district: str


class OrderItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_id: uuid.UUID
    product_name_snapshot: str
    variant_label_snapshot: str | None
    quantity: int
    unit_price: float
    line_total: float


class OrderRead(BaseModel):
    id: uuid.UUID
    order_number: str
    customer_id: uuid.UUID | None
    fulfilling_branch_id: uuid.UUID | None
    guest_full_name: str
    guest_phone_number: str
    guest_email: str | None
    delivery_address: str
    district: str
    status: OrderStatus
    requires_prepayment: bool
    subtotal: float
    total: float
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemRead]


class OrderAddressUpdate(BaseModel):
    # delivery_address stays required (unchanged contract) - the three
    # contact fields are optional additions (FR-ORDER-010 allows editing
    # contact details too, not just the address): omitted means "leave
    # this one as it is", not "clear it".
    delivery_address: str
    guest_full_name: str | None = None
    guest_phone_number: str | None = None
    guest_email: str | None = None


class OrderStatusUpdate(BaseModel):
    to_status: OrderStatus
    notes: str | None = None


class OrderStatusHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    from_status: str | None
    to_status: str
    changed_by_staff_id: uuid.UUID
    notes: str | None
    created_at: datetime


class StaffCreate(BaseModel):
    full_name: str
    email: str
    phone_number: str | None = None
    password: PasswordStr
    # Deliberately no way to request SYSTEM_ADMINISTRATOR through this
    # schema alone - the router additionally rejects it even though
    # someone could technically pass that value here, since Pydantic can't
    # express "this enum value is invalid on this one endpoint."
    role: StaffRole


class StaffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: str
    phone_number: str | None
    role: StaffRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class StaffStatusUpdate(BaseModel):
    # Sets is_active to exactly this value - deliberately NOT a toggle
    # (see routers/staff.py) - a caller says what they want the result to
    # be, rather than "flip whatever's there", so a double-click/retry/
    # replay can't silently reverse the intended change.
    is_active: bool


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    staff_user_id: uuid.UUID
    # NOT auto-mapped off the AuditLog row (from_attributes can't reach
    # across a table it doesn't have a column for) - routers/audit.py
    # builds these explicitly, from a JOIN against staff_users. Nullable
    # because a StaffUser row COULD be gone by the time someone reads this
    # audit entry (test fixtures hard-delete; a real DB admin technically
    # could too, even though the API itself never deletes one) - the
    # audit entry itself should never disappear just because the actor
    # eventually did.
    staff_full_name: str | None
    staff_email: str | None
    action: str
    resource_type: str
    resource_id: uuid.UUID
    previous_value: dict | None
    new_value: dict | None
    created_at: datetime


class DashboardSummary(BaseModel):
    total_orders: int
    pending_orders: int
    total_customers: int
    # None for a Sales Attendant caller - the docs (FR-ADMIN-003) say the
    # API itself filters which widgets a role gets, not just which
    # endpoints; revenue is the one financial figure held back from them.
    total_revenue: float | None


class SalesSummary(BaseModel):
    total_revenue: float
    total_orders: int
    average_order_value: float


class StaffPasswordChange(BaseModel):
    current_password: str
    new_password: PasswordStr


class StaffPasswordResetResult(BaseModel):
    # Not in the original spec - a staff member who forgets their password
    # had no recovery route at all (StaffPasswordChange above needs
    # knowing the CURRENT password, and there's no staff equivalent of
    # /auth/password/forgot). This is the admin-initiated alternative: an
    # Inventory Manager/System Administrator generates a random temporary
    # password server-side and relays it to the locked-out staff member
    # through a trusted channel (in person, a call) - not emailed, so this
    # doesn't need a new frontend page the way the customer flow does.
    temporary_password: str


class StaffUpdate(BaseModel):
    full_name: str
    email: str
    phone_number: str | None = None
    role: StaffRole


class PaymentProvider(str, enum.Enum):
    # Formalizes values that were already de facto in use throughout the
    # codebase (tests, real PesaPal integration) but never actually
    # validated - found 2026-08-06 that Payment.provider accepted ANY
    # string at all, including a typo or an empty one. Deliberately a
    # SCHEMA-only enum, not a models.py/DB one - Payment.provider itself
    # stays a plain VARCHAR(50) (see that column's own comment: the set of
    # providers is expected to grow without needing a migration each
    # time), this just validates what a CLIENT is allowed to send.
    MOBILE_MONEY = "mobile_money"
    CARD = "card"
    CASH_ON_DELIVERY = "cash_on_delivery"


class PaymentInitiate(BaseModel):
    provider: PaymentProvider
    amount: float


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    provider: str
    status: PaymentStatus
    amount: float
    currency: str
    provider_reference: str | None
    failure_reason: str | None
    # Where the frontend needs to send the customer's browser to actually
    # pay (PesaPal's hosted checkout page) - present once the payment is
    # awaiting_payment, null before/after (paid/failed attempts don't need
    # a live checkout link anymore).
    redirect_url: str | None
    initiated_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BannerCreate(BaseModel):
    title: str
    image_url: str
    display_order: int = 0
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class BannerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    image_url: str
    display_order: int
    is_active: bool
    starts_at: datetime | None
    ends_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BannerStatusUpdate(BaseModel):
    # Sets is_active to exactly this value - same "not a toggle" reasoning
    # as StaffStatusUpdate above.
    is_active: bool


class PromotionCreate(BaseModel):
    name: str
    discount_type: DiscountType
    discount_value: float
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class PromotionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    discount_type: DiscountType
    discount_value: float
    starts_at: datetime | None
    ends_at: datetime | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PromotionStatusUpdate(BaseModel):
    # Sets is_active to exactly this value - same "not a toggle" reasoning
    # as StaffStatusUpdate above.
    is_active: bool


class ApplyPromotionRequest(BaseModel):
    # See routers/promotions.py's apply_promotion_to_product - which
    # library Promotion to apply to the product in the URL.
    promotion_id: uuid.UUID


class ProductDiscountCreate(BaseModel):
    discount_type: DiscountType
    discount_value: float
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class ProductDiscountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    # Which library Promotion (if any) this row came from - see the
    # model's own comment on why this is nullable (pre-library discounts
    # are grandfathered in with no link at all).
    promotion_id: uuid.UUID | None
    discount_type: DiscountType
    discount_value: float
    starts_at: datetime | None
    ends_at: datetime | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProductDiscountStatusUpdate(BaseModel):
    # Sets is_active to exactly this value - same "not a toggle" reasoning
    # as StaffStatusUpdate above.
    is_active: bool


class StoreSettingsUpdate(BaseModel):
    catalogue_filter_mode: CatalogueFilterMode


class StoreSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    catalogue_filter_mode: CatalogueFilterMode
    updated_at: datetime


class HomepageSectionCreate(BaseModel):
    # max_length values mirror models.HomepageSection's String(150)/String(500)
    # columns exactly - same reasoning as ProductCreate.description above:
    # without these, an over-length value would pass Pydantic, then crash at
    # the database insert with a raw, uncaught error (a bare-text 500 instead
    # of our usual JSON envelope).
    title: str = Field(max_length=150)
    description: str | None = Field(default=None, max_length=500)
    section_type: HomepageSectionType
    # Required when section_type=by_category, ignored otherwise - enforced
    # in routers/homepage_sections.py (a Pydantic model-level validator
    # would work too, but this project's established pattern is
    # cross-field business rules live in the router, not the schema - see
    # e.g. products' on-sale/promotion check).
    category_id: uuid.UUID | None = None
    display_order: int = 0
    is_enabled: bool = True
    max_products: int | None = None
    view_all_href: str | None = None


class HomepageSectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    section_type: HomepageSectionType
    category_id: uuid.UUID | None
    display_order: int
    is_enabled: bool
    max_products: int | None
    view_all_href: str | None
    created_at: datetime
    updated_at: datetime


class HomepageSectionWithProducts(HomepageSectionRead):
    # Only populated when the public read endpoint is called with
    # include_products=true - see routers/homepage_sections.py. Reuses the
    # full ProductRead shape rather than inventing a slimmer "product
    # summary" schema, matching how this project generally favors one
    # consistent product shape over several narrower ones.
    products: list["ProductRead"]


class HomepageSectionReorder(BaseModel):
    # The full new order, front to back - routers/homepage_sections.py
    # assigns display_order 0, 1, 2... by this list's position, same
    # "caller states the end result" reasoning as the *StatusUpdate
    # schemas above (not a series of individual move-up/move-down calls).
    ordered_ids: list[uuid.UUID]
