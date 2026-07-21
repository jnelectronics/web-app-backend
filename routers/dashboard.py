# Staff-facing operational dashboard. Per FR-ADMIN-003, Sales Attendants
# get a NARROWER view than just "which endpoints can they call" - the
# summary endpoint itself hides the revenue figure from them, even though
# they're allowed to call it at all (see read_dashboard_summary below).

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Customer, InventoryRecord, Order, OrderStatus, StaffRole, StaffUser
from routers.orders import _build_order_read
from schemas import DashboardSummary, InventoryRead, OrderRead, SalesSummary
from security import require_staff_role

router = APIRouter(prefix="/admin/dashboard", tags=["dashboard"], route_class=EnvelopeRoute)

VIEW_SUMMARY_ROLES = (StaffRole.SALES_ATTENDANT, StaffRole.INVENTORY_MANAGER)


def _total_revenue(db: Session) -> float:
    # Cancelled orders never happened financially - excluded from every
    # revenue figure on this dashboard.
    total = (
        db.query(func.sum(Order.total)).filter(Order.status != OrderStatus.CANCELLED).scalar()
    )
    return float(total or 0)


@router.get("/summary", response_model=DashboardSummary)
def read_dashboard_summary(
    current_staff: StaffUser = Depends(require_staff_role(*VIEW_SUMMARY_ROLES)),
    db: Session = Depends(get_db),
):
    total_orders = db.query(Order).count()
    pending_orders = db.query(Order).filter(Order.status == OrderStatus.PENDING).count()
    total_customers = db.query(Customer).count()

    is_inventory_manager = current_staff.role == StaffRole.INVENTORY_MANAGER
    return DashboardSummary(
        total_orders=total_orders,
        pending_orders=pending_orders,
        total_customers=total_customers,
        total_revenue=_total_revenue(db) if is_inventory_manager else None,
    )


@router.get("/recent-orders", response_model=list[OrderRead])
def read_recent_orders(
    limit: int = 10,
    _current_staff: StaffUser = Depends(require_staff_role(*VIEW_SUMMARY_ROLES)),
    db: Session = Depends(get_db),
):
    orders = db.query(Order).order_by(Order.created_at.desc()).limit(limit).all()
    return [_build_order_read(o, db) for o in orders]


@router.get("/low-inventory", response_model=list[InventoryRead])
def read_low_inventory(
    threshold: int = 10,
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    return (
        db.query(InventoryRecord)
        .filter(InventoryRecord.quantity_available <= threshold)
        .order_by(InventoryRecord.quantity_available)
        .all()
    )


@router.get("/sales-summary", response_model=SalesSummary)
def read_sales_summary(
    _current_staff: StaffUser = Depends(require_staff_role(StaffRole.INVENTORY_MANAGER)),
    db: Session = Depends(get_db),
):
    total_orders = db.query(Order).filter(Order.status != OrderStatus.CANCELLED).count()
    total_revenue = _total_revenue(db)
    average_order_value = total_revenue / total_orders if total_orders > 0 else 0.0

    return SalesSummary(
        total_revenue=total_revenue, total_orders=total_orders, average_order_value=average_order_value
    )
