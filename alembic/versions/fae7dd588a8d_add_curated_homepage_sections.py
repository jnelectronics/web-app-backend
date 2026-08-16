"""add curated homepage sections

Revision ID: fae7dd588a8d
Revises: e3af3737819e
Create Date: 2026-08-16 10:14:44.607424

Adds the "curated collection" homepage section type Nyson's 2026-08-13 doc
asks for - staff manually opt individual products into a named collection,
independent of category. Three schema changes:

1. A new 'curated' value on the existing homepage_section_type enum.
2. A new nullable, unique 'slug' column on homepage_sections - required
   (at the application layer, see routers/homepage_sections.py) only when
   section_type='curated'; NULL for every other type, same "only
   meaningful for one type" pattern category_id already uses for
   by_category.
3. A new product_homepage_sections join table - the actual many-to-many
   membership, product <-> curated section.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fae7dd588a8d'
down_revision: Union[str, None] = 'e3af3737819e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres allows adding an enum value inside a transaction (PG12+,
    # which Neon runs), but the new value can't be USED in that same
    # transaction - fine here, since nothing in this migration inserts a
    # row with section_type='curated'.
    op.execute("ALTER TYPE homepage_section_type ADD VALUE IF NOT EXISTS 'curated'")

    op.add_column('homepage_sections', sa.Column('slug', sa.String(length=150), nullable=True))
    op.create_unique_constraint('uq_homepage_sections_slug', 'homepage_sections', ['slug'])

    op.create_table(
        'product_homepage_sections',
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('homepage_section_id', sa.UUID(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], name='fk_product_homepage_sections_product_id'),
        sa.ForeignKeyConstraint(
            ['homepage_section_id'], ['homepage_sections.id'], name='fk_product_homepage_sections_homepage_section_id'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'product_id', 'homepage_section_id', name='uq_product_homepage_sections_product_id_homepage_section_id'
        ),
    )


def downgrade() -> None:
    op.drop_table('product_homepage_sections')
    op.drop_constraint('uq_homepage_sections_slug', 'homepage_sections', type_='unique')
    op.drop_column('homepage_sections', 'slug')
    # Postgres has no "remove enum value" operation - downgrading the
    # 'curated' value itself back out would mean rebuilding the whole
    # enum type. Left alone here, same trade-off this project already
    # accepted for other enum additions; safe as long as no row is using
    # 'curated' at downgrade time (nothing in this migration creates one).
