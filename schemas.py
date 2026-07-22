# This file's job: define the JSON "shapes" the API accepts and returns.
# One pair per domain - a *Create schema (what a client sends us) and a
# *Read schema (what we send back).

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, computed_field

from models import CustomerStatus, CustomerType, DiscountType, MovementType, OrderStatus, PaymentStatus, StaffRole


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


class CategoryCreate(BaseModel):
    name: str
    description: str | None = None


class CategoryRead(BaseModel):
    # Lets Pydantic build this straight from a SQLAlchemy Category object's
    # attributes (e.g. category.name), not just from a plain dict.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
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
    created_at: datetime
    updated_at: datetime


class ProductCreate(BaseModel):
    # The client must say which category this product belongs to -
    # matches the NOT NULL foreign key on the real products table.
    category_id: uuid.UUID
    name: str


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category_id: uuid.UUID
    name: str
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


class ProductImageCreate(BaseModel):
    # No cloudinary_public_id here - that's only ever set by a real
    # Cloudinary upload, which this project doesn't have (see the model's
    # comment). The client just supplies a URL to an already-hosted image.
    image_url: str
    display_order: int = 0


class ProductImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    image_url: str
    cloudinary_public_id: str | None
    is_primary: bool
    display_order: int
    created_at: datetime


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


class CustomerPasswordChange(BaseModel):
    current_password: str
    new_password: PasswordStr


class CustomerLogin(BaseModel):
    email: str
    password: str


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
    quantity: int
    unit_price_snapshot: float

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
    status: OrderStatus
    requires_prepayment: bool
    subtotal: float
    total: float
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemRead]


class OrderAddressUpdate(BaseModel):
    delivery_address: str


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


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    staff_user_id: uuid.UUID
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


class StaffUpdate(BaseModel):
    full_name: str
    email: str
    phone_number: str | None = None
    role: StaffRole


class PaymentInitiate(BaseModel):
    provider: str
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


class ProductDiscountCreate(BaseModel):
    discount_type: DiscountType
    discount_value: float
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class ProductDiscountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    discount_type: DiscountType
    discount_value: float
    starts_at: datetime | None
    ends_at: datetime | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
