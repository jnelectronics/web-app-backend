# This file defines what our database tables actually look like.
# Each class here = one real table in Postgres, once we run a migration for it.

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# Base, and our two shared mixins, come from database.py.
from database import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Category(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    # The actual table name that will exist in Postgres.
    __tablename__ = "categories"

    # String(100) = VARCHAR(100), unique=True means Postgres itself rejects
    # two categories with the same name.
    name: Mapped[str] = mapped_column(String(100), unique=True)

    # `str | None` (instead of just `str`) means this column is optional/nullable.
    description: Mapped[str | None] = mapped_column(Text)

    # Soft-delete flag: per the docs, categories/products are never actually
    # removed from the database, just deactivated. default=True means new
    # categories start out active.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Branch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "branches"

    name: Mapped[str] = mapped_column(String(150))
    address: Mapped[str] = mapped_column(String(255))

    # `str | None` = optional column - per the docs, phone/email aren't
    # required for a branch to exist.
    phone_number: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "products"

    # ForeignKey("categories.id") makes this column point at a real row in
    # the categories table - Postgres itself will reject a product with a
    # category_id that doesn't exist.
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"))

    name: Mapped[str] = mapped_column(String(200))

    # Longer marketing copy for the product detail page - nullable since a
    # product can exist (e.g. just created) before anyone's written this yet.
    description: Mapped[str | None] = mapped_column(String(2000))

    # For the homepage's featured-products rail - a plain manual flag, not
    # derived from anything else.
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)

    # URL-friendly identifier for product detail pages (/products/{slug}
    # instead of /products/{uuid}) - server-generated once at creation time
    # (see routers/products.py's _generate_unique_slug) and never changed
    # afterward, even if the name is later edited, so existing links/SEO
    # never break out from under a customer.
    #
    # The `default=` here is a FALLBACK only, for tests/scripts that
    # construct a Product directly via the ORM instead of going through
    # POST /products - it guarantees a unique value exists either way
    # (the column is NOT NULL + unique) without every existing test
    # fixture needing to be touched. Real product creation always
    # overrides this with a real, name-derived slug.
    slug: Mapped[str] = mapped_column(
        String(220), unique=True, default=lambda: f"product-{uuid.uuid4().hex[:8]}"
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # NOTE: is_discounted is deliberately NOT a column here - product_discounts
    # (see ProductDiscount below) has its own starts_at/ends_at/is_active
    # window, so "is this product currently discounted" is a question that
    # can only be answered by checking the CURRENT time against that table,
    # not something safe to cache as a static boolean that could drift out
    # of sync. Computed at read time instead - see
    # routers/products.py's _build_product_read.


class ProductVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    # Per the docs, this is what actually gets bought/stocked/priced - a
    # Product is just the shared "listing" (name, category); each variant
    # is one purchasable option under it (e.g. a specific color/size),
    # with its own SKU and price. Inventory tracks stock per-variant,
    # per-branch - never per-product directly.
    __tablename__ = "product_variants"

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))

    # unique=True: no two variants anywhere can share a SKU, since it's
    # meant to uniquely identify a purchasable item across the whole catalog.
    sku: Mapped[str] = mapped_column(String(64), unique=True)

    # Denormalized human-readable label, e.g. "128GB / Midnight Black" -
    # stored directly rather than built from variant_attributes each time,
    # since we're not building the attributes EAV table yet.
    variant_label: Mapped[str | None] = mapped_column(String(150))

    price: Mapped[float]

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ProductImage(UUIDPrimaryKeyMixin, Base):
    # No TimestampMixin - the docs only list created_at for this table (an
    # image row is replaced via PUT by creating/pointing at a new one in
    # practice, not edited in place, so there's nothing to track an
    # updated_at for).
    __tablename__ = "product_images"

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    image_url: Mapped[str] = mapped_column(String(500))
    # Set by a real Cloudinary upload in production - always NULL here,
    # since (like Payments' gateway) no Cloudinary client exists in this
    # project; images are just URLs the client already has hosted somewhere.
    cloudinary_public_id: Mapped[str | None] = mapped_column(String(255))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # Partial unique index (BR-PROD-005): only rows where is_primary is
        # true are checked, so a product can freely have several
        # non-primary images but never two marked primary at once.
        Index(
            "uq_product_images_one_primary",
            "product_id",
            unique=True,
            postgresql_where=text("is_primary = true"),
        ),
    )


class VariantAttribute(UUIDPrimaryKeyMixin, Base):
    # No timestamps at all - the docs don't list any for this table. It's
    # pure EAV data (variant_id, attribute_name, attribute_value) with
    # nothing else to track.
    __tablename__ = "variant_attributes"

    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_variants.id"))
    attribute_name: Mapped[str] = mapped_column(String(50))
    attribute_value: Mapped[str] = mapped_column(String(150))

    __table_args__ = (
        UniqueConstraint("variant_id", "attribute_name", name="uq_variant_attribute_name"),
    )


class InventoryRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    # One row per (variant, branch) pair - e.g. "10 units of the Black
    # FreePods 4 variant at the Westlands branch". This is deliberately its
    # own table rather than columns on ProductVariant, since a variant's
    # stock differs branch to branch.
    __tablename__ = "inventory_records"

    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_variants.id"))
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("branches.id"))

    # Integer, not float - you can't have half a unit of stock.
    quantity_available: Mapped[int] = mapped_column(Integer, default=0)

    # Stock that's set aside for an active cart/order but not yet sold -
    # kept separate from quantity_available so "in stock" numbers don't
    # include units someone else has already claimed.
    quantity_reserved: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        # Mirrors the docs' CHECK constraints - Postgres itself will
        # refuse a row where either quantity would go negative, as a last
        # line of defense even if application code has a bug.
        CheckConstraint("quantity_available >= 0", name="ck_quantity_available_non_negative"),
        CheckConstraint("quantity_reserved >= 0", name="ck_quantity_reserved_non_negative"),
        # One branch can only have ONE stock row per variant - stock
        # adjustments update that single row rather than ever inserting
        # a duplicate for the same (variant, branch) pair.
        UniqueConstraint("variant_id", "branch_id", name="uq_inventory_variant_branch"),
    )


# A plain Python enum - just a fixed set of allowed values. Passing it to
# sqlalchemy's Enum() below makes Postgres create a REAL enum type in the
# database (not just a VARCHAR), so the database itself rejects any value
# outside this list, on top of Pydantic validating it on the way in.
class AuditLog(UUIDPrimaryKeyMixin, Base):
    # Write-once, never modified or deleted (FR-AUDIT-004/005) - no
    # TimestampMixin, no updated_at, same reasoning as the other
    # append-only logs in this file (OrderStatusHistory, InventoryMovement).
    __tablename__ = "audit_logs"

    staff_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_users.id"))
    # e.g. "product.update", "order.status_change" - a free-text action
    # name, not an enum, since the docs give examples rather than a fixed
    # list and new action types shouldn't need a migration to add.
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(100))
    # No real ForeignKey - resource_id is polymorphic (could point at a
    # product, an order, an inventory record, ...) depending on
    # resource_type, same reasoning as RefreshToken.owner_id.
    resource_id: Mapped[uuid.UUID]

    # JSONB (not a fixed set of columns) since what "before/after" even
    # MEANS is different for every resource_type - a product update and an
    # inventory adjustment have nothing in common to store as typed columns.
    previous_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MovementType(str, enum.Enum):
    STOCK_IN = "stock_in"
    STOCK_OUT = "stock_out"
    RESERVED = "reserved"
    SOLD = "sold"
    ADJUSTMENT = "adjustment"


class InventoryMovement(UUIDPrimaryKeyMixin, Base):
    # Immutable, append-only audit trail (per the docs) - no TimestampMixin,
    # same reasoning as RefreshToken/OrderStatusHistory: written once,
    # never edited.
    __tablename__ = "inventory_movements"

    inventory_record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_records.id"))
    movement_type: Mapped[MovementType] = mapped_column(
        Enum(MovementType, name="movement_type", values_callable=lambda e: [x.value for x in e])
    )
    # Signed - positive for stock coming in, negative for stock leaving,
    # same convention as InventoryAdjust.quantity_change already uses.
    quantity_changed: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(255))

    # NULL for a system-generated 'sold' movement (checkout has no staff
    # actor - the customer isn't a staff_users row) - set for anything a
    # staff member did directly, like a manual adjustment.
    staff_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    # Set when this movement came from an order (checkout decrementing
    # stock, or a cancellation restoring it) - NULL for a standalone manual
    # adjustment that has nothing to do with any specific order.
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CustomerType(str, enum.Enum):
    GUEST = "guest"
    REGISTERED = "registered"


class CustomerStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    # Both guest and registered customers live in this one table, per the
    # docs - customer_type tells them apart. A guest checking out never
    # actually creates a row here (that happens elsewhere, via a guest
    # token) - this table/model is for the Auth flows: registering and
    # logging in as a real account.
    __tablename__ = "customers"

    # values_callable tells SQLAlchemy to store the enum's VALUES
    # ("guest", "registered") as the actual Postgres labels - without it,
    # SQLAlchemy defaults to storing the member NAMES ("GUEST", "REGISTERED"),
    # which wouldn't match the lowercase values the docs specify.
    customer_type: Mapped[CustomerType] = mapped_column(
        Enum(CustomerType, name="customer_type", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        default=CustomerType.REGISTERED,
    )

    full_name: Mapped[str | None] = mapped_column(String(150))

    # unique=True + nullable: an email/phone must be unique if present, but
    # multiple guest rows (which won't exist via this flow) could otherwise
    # all have NULL - Postgres treats NULLs as distinct from each other, so
    # this doesn't block having many rows with no email.
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), unique=True)

    # NULL for guest customers - only registered accounts have a password.
    password_hash: Mapped[str | None] = mapped_column(String(255))

    status: Mapped[CustomerStatus] = mapped_column(
        Enum(CustomerStatus, name="customer_status", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        default=CustomerStatus.ACTIVE,
    )


class CartStatus(str, enum.Enum):
    ACTIVE = "active"
    CONVERTED = "converted"
    ABANDONED = "abandoned"


class Cart(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "carts"

    # Both nullable - a cart belongs to EITHER a registered customer OR a
    # guest session, never both at once. Which one is NULL is what tells
    # the two cases apart.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"))
    # NOT unique=True here - see the partial index below for why a plain
    # column-level unique constraint is wrong for this column specifically.
    guest_token: Mapped[str | None] = mapped_column(String(100))

    status: Mapped[CartStatus] = mapped_column(
        Enum(CartStatus, name="cart_status", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        default=CartStatus.ACTIVE,
    )

    __table_args__ = (
        # Mirrors the docs' CHECK - Postgres itself refuses a cart row
        # that's neither owned by a customer nor a guest session.
        CheckConstraint(
            "customer_id IS NOT NULL OR guest_token IS NOT NULL",
            name="ck_cart_has_owner",
        ),
        # A PARTIAL unique index (found 2026-08-06, same pattern as
        # Payment's uq_payments_order_paid below) - only ACTIVE carts are
        # checked for uniqueness, so a guest_token that's already been used
        # by an OLDER, now-converted/abandoned cart can be reused for a
        # brand new active one. A plain column-level unique=True (the
        # original setup) made this impossible: the very first time a
        # guest's cart converted (via checkout OR the new login-time merge
        # below), that guest_token became permanently unusable - the next
        # time the same browser tried to add anything to cart,
        # get_current_cart's own fallback (create a new Cart for this
        # token) would hit that same old unique constraint and crash with
        # a raw database error instead of quietly starting a fresh cart.
        Index(
            "uq_carts_guest_token_active",
            "guest_token",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class CartItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cart_items"

    cart_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("carts.id"))
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_variants.id"))

    quantity: Mapped[int] = mapped_column(Integer)

    # Copied from the variant's price at the moment this item was added -
    # if the variant's price changes later, items already sitting in a
    # cart keep showing the price the customer originally saw.
    unit_price_snapshot: Mapped[float]

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_cart_item_quantity_positive"),
        # One line per variant per cart - adding the same variant again
        # increments this row's quantity instead of inserting a duplicate.
        UniqueConstraint("cart_id", "variant_id", name="uq_cart_item_cart_variant"),
    )


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PACKED = "packed"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"

    # Human-friendly identifier ("JN-20260712-0001") shown to
    # customers/staff - the real primary key is still the UUID id, this is
    # just what a person reads/quotes over the phone.
    order_number: Mapped[str] = mapped_column(String(30), unique=True)

    # NULL for a guest order - there's no customer row to point at.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"))

    # Copied from Cart.guest_token at checkout time (NULL for a registered
    # customer's order, same as customer_id is NULL for a guest's) - this
    # is what lets a guest, who has no account/login at all, prove after
    # the fact that a given order is "theirs" when paying for it or
    # checking payment status (routers/payments.py) - the same
    # X-Guest-Token header already used for cart operations, just checked
    # against this snapshot instead of a live Cart row.
    guest_token: Mapped[str | None] = mapped_column(String(100))

    # NULL until a branch with enough stock is found during checkout.
    fulfilling_branch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("branches.id"))

    # Always collected, even for a registered customer, since the docs'
    # checkout example takes this per-order - keeps delivery details tied
    # to the specific order rather than only the account's profile.
    guest_full_name: Mapped[str] = mapped_column(String(150))
    guest_phone_number: Mapped[str] = mapped_column(String(20))
    guest_email: Mapped[str | None] = mapped_column(String(255))

    delivery_address: Mapped[str] = mapped_column(String(500))

    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        default=OrderStatus.PENDING,
    )

    # Whether this order must be paid online before staff will process it -
    # always false for now since Payments (Phase 6) isn't built yet.
    requires_prepayment: Mapped[bool] = mapped_column(Boolean, default=False)

    # subtotal and total are the same for now (no delivery fee/tax/discount
    # logic yet) but kept as separate columns since the docs treat them as
    # distinct - future phases (Promotions) will make them diverge.
    subtotal: Mapped[float]
    total: Mapped[float]


class OrderItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product_variants.id"))

    # Snapshotted at checkout, same reasoning as CartItem.unit_price_snapshot
    # - so a historical order still reads correctly even if the product is
    # later renamed, the variant relabeled, or the price changes.
    product_name_snapshot: Mapped[str] = mapped_column(String(200))
    variant_label_snapshot: Mapped[str | None] = mapped_column(String(150))

    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float]
    line_total: Mapped[float]

    __table_args__ = (CheckConstraint("quantity > 0", name="ck_order_item_quantity_positive"),)


class OrderStatusHistory(UUIDPrimaryKeyMixin, Base):
    # Immutable, append-only log (per the docs) - no TimestampMixin, same
    # reasoning as RefreshToken: a row is written once and never touched
    # again, so there's no meaningful "updated_at".
    __tablename__ = "order_status_history"

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))

    # Plain strings, not the order_status ENUM - matches the DB doc exactly.
    # Nullable from_status is for a future "created" entry (docs: "null on
    # initial creation") - not written anywhere yet, since checkout doesn't
    # currently log its own initial 'pending' row.
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))

    # NOT NULL per the docs - this log only ever records STAFF-driven
    # transitions (the staff-only PATCH /orders/{id}/status endpoint). A
    # customer's own self-service cancel (PATCH /orders/{id}/cancel) has no
    # staff actor to attribute here, so it deliberately isn't logged to
    # this table.
    changed_by_staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_users.id"))
    notes: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    FAILED = "failed"


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    # One row per payment ATTEMPT, not one row per order - per the docs, a
    # failed attempt must stay on record even after a later attempt for the
    # same order succeeds, so this table can have many rows pointing at one
    # order (e.g. a declined card, then a successful mobile money retry).
    __tablename__ = "payments"

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))

    # e.g. "mobile_money", "card", "cash_on_delivery" - a plain string, not
    # a Postgres enum, since the docs list it as VARCHAR(50) (the set of
    # providers is expected to grow without needing a migration each time).
    provider: Mapped[str] = mapped_column(String(50))

    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status", values_callable=lambda e: [x.value for x in e]),
        default=PaymentStatus.PENDING,
    )

    amount: Mapped[float]
    currency: Mapped[str] = mapped_column(String(3), default="UGX")

    # PesaPal's own order_tracking_id (see pesapal_client.py) - what the
    # IPN callback and GetTransactionStatus calls use to identify which
    # payment attempt they're talking about. Set right after
    # submit_order_request() succeeds, in routers/payments.py.
    provider_reference: Mapped[str | None] = mapped_column(String(150))
    failure_reason: Mapped[str | None] = mapped_column(String(255))

    # PesaPal's hosted checkout page for this attempt - the customer's
    # browser needs to be sent here to actually pay. Persisted (not just
    # returned once) so GET /payments/{id} can still show it while the
    # payment is sitting in awaiting_payment.
    redirect_url: Mapped[str | None] = mapped_column(String(500))

    initiated_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]

    __table_args__ = (
        # A PARTIAL unique index - only rows where status = 'paid' are
        # checked for uniqueness, so many 'failed'/'awaiting_payment' rows
        # can pile up for the same order, but Postgres itself refuses a
        # second 'paid' row for it (BR-PAY-005, mirrored at the app level
        # too by DuplicatePaymentError in routers/payments.py, which gives
        # a clean 409 instead of a raw database error reaching the client).
        Index(
            "uq_payments_order_paid",
            "order_id",
            unique=True,
            postgresql_where=text("status = 'paid'"),
        ),
    )


class OwnerType(str, enum.Enum):
    CUSTOMER = "customer"
    STAFF = "staff"


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    # No TimestampMixin here (unlike every other table) - per the docs this
    # table only ever needs created_at, not updated_at, since a row is
    # never edited after being written, only marked revoked_at once.
    __tablename__ = "refresh_tokens"

    # "Polymorphic" - owner_id can point at either customers.id or
    # staff_users.id, told apart by owner_type, so there's deliberately no
    # real ForeignKey here (a DB FK can only ever point at one table).
    owner_type: Mapped[OwnerType] = mapped_column(
        Enum(OwnerType, name="owner_type", values_callable=lambda e: [x.value for x in e])
    )
    owner_id: Mapped[uuid.UUID]

    # We never store the raw refresh token itself, same reasoning as never
    # storing a raw password - only its hash. Unlike passwords though, this
    # uses a fast SHA-256 hash (see security.py's hash_refresh_token), not
    # slow Argon2: a refresh token is already a long random secret (not a
    # human-guessable password), so there's no brute-force risk to slow
    # down, and this hash gets checked on every single token refresh.
    token_hash: Mapped[str] = mapped_column(String(255), unique=True)

    # Explicit timezone=True (unlike Payment's initiated_at/completed_at,
    # which are informational only) - these two get COMPARED against
    # datetime.now(timezone.utc) in routers/auth.py, and comparing a
    # timezone-aware datetime against a naive one raises a TypeError.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Banner(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "banners"

    title: Mapped[str] = mapped_column(String(150))
    image_url: Mapped[str] = mapped_column(String(500))
    # Lower numbers show first - lets staff reorder banners without
    # having to delete/recreate them in a different order.
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Both optional - a banner with neither is just "active until turned
    # off manually" (is_active is the only gate). Set either/both to run it
    # on a schedule instead.
    starts_at: Mapped[datetime | None]
    ends_at: Mapped[datetime | None]


class DiscountType(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


class ProductDiscount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    # Its own table rather than columns on Product (per the docs) - so a
    # product can have a history of discount windows over time, and a
    # future one can be scheduled before the current one even ends.
    __tablename__ = "product_discounts"

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))

    discount_type: Mapped[DiscountType] = mapped_column(
        Enum(DiscountType, name="discount_type", values_callable=lambda e: [x.value for x in e])
    )
    # Meaning depends on discount_type: a percentage (e.g. 15 = 15% off) or
    # a flat currency amount off - the application layer interprets which,
    # this column just stores whatever number applies.
    discount_value: Mapped[float]

    starts_at: Mapped[datetime | None]
    ends_at: Mapped[datetime | None]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class StaffRole(str, enum.Enum):
    SYSTEM_ADMINISTRATOR = "system_administrator"
    INVENTORY_MANAGER = "inventory_manager"
    SALES_ATTENDANT = "sales_attendant"


class StaffUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    # Internal accounts only - completely separate table from Customer.
    # There's no public "register as staff" endpoint (see routers/staff.py):
    # staff accounts are created BY an existing Inventory Manager/System
    # Administrator, and the very first System Administrator is created
    # outside the API entirely, by seed_admin.py.
    __tablename__ = "staff_users"

    full_name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(255), unique=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    role: Mapped[StaffRole] = mapped_column(
        Enum(StaffRole, name="staff_role", values_callable=lambda enum_cls: [e.value for e in enum_cls])
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
