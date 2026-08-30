# Staff-facing operational dashboard.
#
# Sales Attendant access to this whole module was REVOKED on 2026-08-30, at
# the client's explicit request during UAT ("Remove Dashboard, Sales, and
# Payments access from Sales Attendants") - this reverses the 2026-08-18
# widening for this one module specifically, everywhere else that widening
# still stands. System Administrator is unaffected either way - it's a
# true superset role handled centrally in security.py's require_staff_role,
# not something that has to be listed in these tuples at all.

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from envelope import EnvelopeRoute
from models import Customer, InventoryRecord, Order, OrderStatus, Payment, PaymentStatus, StaffRole, StaffUser
from routers.orders import _build_order_read
from schemas import DashboardSummary, InventoryRead, OrderRead, SalesSummary
from security import require_staff_role

router = APIRouter(prefix="/admin/dashboard", tags=["dashboard"], route_class=EnvelopeRoute)

VIEW_SUMMARY_ROLES = (StaffRole.OWNER,)


def _total_revenue(db: Session) -> float:
    # "Revenue" means money actually received, not money invoiced - an
    # order sitting unpaid (or one whose only payment attempt FAILED)
    # hasn't earned anything yet, even though it's a real, non-cancelled
    # order. So this only sums orders that have at least one PAID payment
    # row, found via a subquery of order_ids from the payments table.
    # Cancelled orders are excluded too, mostly as a belt-and-braces check -
    # in practice a cancelled order should never have a PAID payment
    # anyway, but there's no DB constraint enforcing that today.
    paid_order_ids = db.query(Payment.order_id).filter(Payment.status == PaymentStatus.PAID)
    total = (
        db.query(func.sum(Order.total))
        .filter(Order.status != OrderStatus.CANCELLED, Order.id.in_(paid_order_ids))
        .scalar()
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

    # System Administrator sees everything Owner sees here too - it's a
    # true superset role (see security.py's require_staff_role), not just
    # another name that has to be listed explicitly. Revenue visibility
    # stays narrower than the rest of the RBAC widening below (FR-ADMIN-003,
    # unaffected by the 2026-08-18 Sales Attendant access expansion) -
    # Sales Attendant is deliberately still excluded here.
    can_see_revenue = current_staff.role in (
        StaffRole.OWNER,
        StaffRole.SYSTEM_ADMINISTRATOR,
    )
    return DashboardSummary(
        total_orders=total_orders,
        pending_orders=pending_orders,
        total_customers=total_customers,
        total_revenue=_total_revenue(db) if can_see_revenue else None,
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
    _current_staff: StaffUser = Depends(require_staff_role(*VIEW_SUMMARY_ROLES)),
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
    _current_staff: StaffUser = Depends(require_staff_role(*VIEW_SUMMARY_ROLES)),
    db: Session = Depends(get_db),
):
    total_orders = db.query(Order).filter(Order.status != OrderStatus.CANCELLED).count()
    total_revenue = _total_revenue(db)
    average_order_value = total_revenue / total_orders if total_orders > 0 else 0.0

    return SalesSummary(
        total_revenue=total_revenue, total_orders=total_orders, average_order_value=average_order_value
    )
