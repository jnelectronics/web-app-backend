"""fix homepage section seed order and max_products

Revision ID: e3af3737819e
Revises: 9efc87f6f25a
Create Date: 2026-08-11 20:32:58.861710

Data-only fix, no schema change - the original seed in 9efc87f6f25a used
Featured/On Sale/New Arrivals as its display order and left max_products
NULL (relying on a "frontend defaults to 8" assumption baked into that
migration's own comment). Nyson's integration doc (2026-08-11) specifies
the real frontend order (On Sale/Featured/New Arrivals) and default cap
(12, not 8) - this migration re-points the 3 already-seeded rows at those
values by title, so the shared dev/prod DB matches what the frontend
actually expects instead of a guess made before the doc existed.

Matches by title (and only rows that still hold the exact values the
original seed wrote) rather than blindly overwriting every row, so this
is a no-op - not a clobber - for any of the 3 seeded rows an admin has
since edited by hand through the (soon-to-exist) admin UI.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3af3737819e'
down_revision: Union[str, None] = '9efc87f6f25a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


homepage_sections_table = sa.table(
    "homepage_sections",
    sa.column("title", sa.String),
    sa.column("display_order", sa.Integer),
    sa.column("max_products", sa.Integer),
)

# (title, old_display_order, new_display_order)
_ORDER_FIX = [
    ("On Sale", 1, 0),
    ("Featured Products", 0, 1),
    ("New Arrivals", 2, 2),
]


def upgrade() -> None:
    connection = op.get_bind()
    for title, old_order, new_order in _ORDER_FIX:
        connection.execute(
            homepage_sections_table.update()
            .where(
                homepage_sections_table.c.title == title,
                homepage_sections_table.c.display_order == old_order,
                homepage_sections_table.c.max_products.is_(None),
            )
            .values(display_order=new_order, max_products=12)
        )


def downgrade() -> None:
    connection = op.get_bind()
    for title, old_order, new_order in _ORDER_FIX:
        connection.execute(
            homepage_sections_table.update()
            .where(
                homepage_sections_table.c.title == title,
                homepage_sections_table.c.display_order == new_order,
                homepage_sections_table.c.max_products == 12,
            )
            .values(display_order=old_order, max_products=None)
        )
