"""remove branches and collapse inventory to global stock

Revision ID: 49b073385187
Revises: 45bf0f5a35f2
Create Date: 2026-08-20 16:19:15.095158

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '49b073385187'
down_revision: Union[str, None] = '45bf0f5a35f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Autogenerate's own ordering put drop_table('branches') BEFORE the two
    # foreign keys pointing at it were dropped - real Postgres would reject
    # that (can't drop a table something else still references), so this
    # was reordered by hand: both FKs (+ the columns that carried them) go
    # first, branches itself is dropped last, once nothing points at it
    # anymore. This is also a genuinely hard drop, not a soft-delete/data
    # migration - safe here specifically because the Neon DB this runs
    # against was just recreated empty (see CLAUDE.md's 2026-08-20 note);
    # a repo with real branch/inventory data would need a real backfill
    # plan instead of a straight DROP.
    op.drop_constraint('inventory_records_branch_id_fkey', 'inventory_records', type_='foreignkey')
    op.drop_constraint('uq_inventory_variant_branch', 'inventory_records', type_='unique')
    op.create_unique_constraint('uq_inventory_variant', 'inventory_records', ['variant_id'])
    op.drop_column('inventory_records', 'branch_id')
    op.drop_constraint('orders_fulfilling_branch_id_fkey', 'orders', type_='foreignkey')
    op.drop_column('orders', 'fulfilling_branch_id')
    op.drop_index('uq_branches_single_default', table_name='branches', postgresql_where='(is_default = true)')
    op.drop_table('branches')


def downgrade() -> None:
    # Mirrors upgrade()'s reordering: branches has to exist again BEFORE
    # anything can create a foreign key pointing back at it.
    op.create_table('branches',
    sa.Column('name', sa.VARCHAR(length=150), autoincrement=False, nullable=False),
    sa.Column('address', sa.VARCHAR(length=255), autoincrement=False, nullable=False),
    sa.Column('phone_number', sa.VARCHAR(length=20), autoincrement=False, nullable=True),
    sa.Column('email', sa.VARCHAR(length=255), autoincrement=False, nullable=True),
    sa.Column('is_active', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('is_default', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name='branches_pkey')
    )
    op.create_index('uq_branches_single_default', 'branches', ['is_default'], unique=True, postgresql_where='(is_default = true)')
    op.add_column('orders', sa.Column('fulfilling_branch_id', sa.UUID(), autoincrement=False, nullable=True))
    op.create_foreign_key('orders_fulfilling_branch_id_fkey', 'orders', 'branches', ['fulfilling_branch_id'], ['id'])
    op.add_column('inventory_records', sa.Column('branch_id', sa.UUID(), autoincrement=False, nullable=False))
    op.drop_constraint('uq_inventory_variant', 'inventory_records', type_='unique')
    op.create_unique_constraint('uq_inventory_variant_branch', 'inventory_records', ['variant_id', 'branch_id'])
    op.create_foreign_key('inventory_records_branch_id_fkey', 'inventory_records', 'branches', ['branch_id'], ['id'])
