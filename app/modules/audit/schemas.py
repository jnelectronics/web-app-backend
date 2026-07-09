import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
