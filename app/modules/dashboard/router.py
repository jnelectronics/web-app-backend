from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db_session, require_roles
from app.db.enums import StaffRole
from app.modules.dashboard.schemas import DashboardSummary
from app.modules.dashboard.service import DashboardService
from app.utils.responses import success_envelope

router = APIRouter(prefix="/admin", tags=["Admin Dashboard"])


@router.get("/dashboard/summary")
def get_dashboard_summary(
    principal=Depends(
        require_roles(StaffRole.INVENTORY_MANAGER.value, StaffRole.SYSTEM_ADMINISTRATOR.value)
    ),
    db: Session = Depends(get_db_session),
):
    summary: DashboardSummary = DashboardService(db).get_summary()
    return success_envelope(summary, "Dashboard summary retrieved successfully.")


# TODO: sales/inventory breakdowns per API Specification §5.12.
