from sqlalchemy.orm import Session

from app.db.enums import OrderStatus
from app.modules.dashboard.schemas import DashboardSummary
from app.modules.inventory.models import InventoryRecord
from app.modules.orders.models import Order
from app.modules.users.models import Customer

LOW_STOCK_THRESHOLD = 5


class DashboardService:
    """FR-DASH-001-006: read-only aggregate view for the admin dashboard."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_summary(self) -> DashboardSummary:
        return DashboardSummary(
            total_orders=self.db.query(Order).count(),
            pending_orders=self.db.query(Order).filter(Order.status == OrderStatus.PENDING).count(),
            total_customers=self.db.query(Customer).count(),
            low_stock_variants=self.db.query(InventoryRecord)
            .filter(InventoryRecord.quantity_available < LOW_STOCK_THRESHOLD)
            .count(),
        )
