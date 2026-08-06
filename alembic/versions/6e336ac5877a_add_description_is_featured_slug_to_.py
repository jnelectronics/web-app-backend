"""add description is_featured slug to products

Revision ID: 6e336ac5877a
Revises: e5dfc75a77e8
Create Date: 2026-08-05 20:41:38.790211

"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '6e336ac5877a'
down_revision: Union[str, None] = 'e5dfc75a77e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Deliberately a standalone copy, not imported from routers/products.py -
# migrations should stay self-contained, since application code (and this
# exact slug-generation logic) is free to change later without rewriting
# history.
def _slugify(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or "product"


def upgrade() -> None:
    op.add_column('products', sa.Column('description', sa.String(length=2000), nullable=True))
    op.add_column(
        'products',
        sa.Column('is_featured', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    # slug starts NULLABLE so the existing rows below can be backfilled
    # first - a NOT NULL UNIQUE column can't be added directly to a table
    # that already has rows with nothing to put in it.
    op.add_column('products', sa.Column('slug', sa.String(length=220), nullable=True))

    connection = op.get_bind()
    products_table = sa.table(
        "products",
        sa.column("id", UUID),
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
    )
    existing_products = connection.execute(sa.select(products_table.c.id, products_table.c.name)).fetchall()

    used_slugs = set()
    for product_id, name in existing_products:
        base_slug = _slugify(name)
        slug = base_slug
        suffix = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        used_slugs.add(slug)
        connection.execute(
            products_table.update().where(products_table.c.id == product_id).values(slug=slug)
        )

    op.alter_column('products', 'slug', nullable=False)
    op.create_unique_constraint('uq_products_slug', 'products', ['slug'])


def downgrade() -> None:
    op.drop_constraint('uq_products_slug', 'products', type_='unique')
    op.drop_column('products', 'slug')
    op.drop_column('products', 'is_featured')
    op.drop_column('products', 'description')
