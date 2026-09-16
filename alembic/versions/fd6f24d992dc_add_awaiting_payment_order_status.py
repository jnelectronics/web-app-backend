"""add awaiting_payment order status

Revision ID: fd6f24d992dc
Revises: d0ef9e4ae56d
Create Date: 2026-09-16 16:26:14.114534

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fd6f24d992dc'
down_revision: Union[str, None] = 'd0ef9e4ae56d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Client-requested (Norman, 2026-09-16): every order must be paid
    # before it's placed. New first stage of the order_status lifecycle -
    # see models.py's OrderStatus for the full story. Placed BEFORE
    # 'pending' purely so the enum's own declared order reads the same as
    # the real lifecycle order - nothing in this codebase actually
    # compares OrderStatus values positionally, so this ordering is
    # cosmetic, not load-bearing.
    op.execute("ALTER TYPE order_status ADD VALUE 'awaiting_payment' BEFORE 'pending'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE at all - removing an enum
    # value means recreating the whole type (and every column using it),
    # which only makes sense if no row anywhere still uses the value being
    # removed. Not attempted here: by the time anyone would run this
    # downgrade, real awaiting_payment orders likely already exist. If this
    # migration is ever rolled back, do it by hand after confirming (or
    # migrating away) any awaiting_payment rows first.
    pass
