from pydantic import BaseModel


class DashboardSummary(BaseModel):
    total_orders: int
    pending_orders: int
    total_customers: int
    low_stock_variants: int
