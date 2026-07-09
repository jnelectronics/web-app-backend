"""Python-side mirrors of the PostgreSQL ENUM types (DB Design Doc §6)."""

import enum


class StaffRole(str, enum.Enum):
    SYSTEM_ADMINISTRATOR = "system_administrator"
    INVENTORY_MANAGER = "inventory_manager"
    SALES_ATTENDANT = "sales_attendant"


class CustomerType(str, enum.Enum):
    GUEST = "guest"
    REGISTERED = "registered"


class CustomerStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class OwnerType(str, enum.Enum):
    CUSTOMER = "customer"
    STAFF = "staff"


class CartStatus(str, enum.Enum):
    ACTIVE = "active"
    CONVERTED = "converted"
    ABANDONED = "abandoned"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PACKED = "packed"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    FAILED = "failed"


class MovementType(str, enum.Enum):
    STOCK_IN = "stock_in"
    STOCK_OUT = "stock_out"
    RESERVED = "reserved"
    SOLD = "sold"
    ADJUSTMENT = "adjustment"


class DiscountType(str, enum.Enum):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
