"""add category groups, homepage sections, promotion library, and product form flags

Revision ID: 9efc87f6f25a
Revises: d68f504c3052
Create Date: 2026-08-09 20:51:57.315829

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '9efc87f6f25a'
down_revision: Union[str, None] = 'd68f504c3052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- category_groups ---------------------------------------------------
    op.create_table('category_groups',
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('icon', sa.String(length=50), nullable=True),
        sa.Column('header_rank', sa.Integer(), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('header_rank IS NULL OR header_rank BETWEEN 1 AND 5', name='ck_category_groups_header_rank_range'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('header_rank'),
        sa.UniqueConstraint('name'),
    )

    # --- promotions (the library) -------------------------------------------
    op.create_table('promotions',
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('discount_type', sa.Enum('percentage', 'fixed_amount', name='promotion_discount_type'), nullable=False),
        sa.Column('discount_value', sa.Float(), nullable=False),
        sa.Column('starts_at', sa.DateTime(), nullable=True),
        sa.Column('ends_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # --- store_settings (singleton - exactly one row, seeded below) --------
    op.create_table('store_settings',
        sa.Column('catalogue_filter_mode', sa.Enum('categories', 'category_groups', name='catalogue_filter_mode'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # --- homepage_sections ----------------------------------------------------
    op.create_table('homepage_sections',
        sa.Column('title', sa.String(length=150), nullable=False),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('section_type', sa.Enum('all_products', 'featured', 'on_sale', 'new_arrival', 'by_category', name='homepage_section_type'), nullable=False),
        sa.Column('category_id', sa.UUID(), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False),
        sa.Column('max_products', sa.Integer(), nullable=True),
        sa.Column('view_all_href', sa.String(length=255), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name='fk_homepage_sections_category_id'),
        sa.PrimaryKeyConstraint('id'),
    )

    # --- branches.is_default -------------------------------------------------
    # server_default so the 14 EXISTING branch rows get a real value at
    # ALTER TABLE time, instead of adding a NOT NULL column with nothing
    # to put in it - same pattern 6e336ac5877a already used for
    # products.is_featured.
    op.add_column('branches', sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.create_index('uq_branches_single_default', 'branches', ['is_default'], unique=True, postgresql_where=sa.text('is_default = true'))

    # --- categories.category_group_id (required, needs a real backfill) -----
    # Starts NULLABLE so the existing rows below can be backfilled first -
    # same "nullable-then-backfill-then-NOT-NULL" shape 6e336ac5877a used
    # for products.slug.
    op.add_column('categories', sa.Column('category_group_id', sa.UUID(), nullable=True))

    connection = op.get_bind()

    category_groups_table = sa.table(
        "category_groups",
        sa.column("id", UUID),
        sa.column("name", sa.String),
        sa.column("icon", sa.String),
        sa.column("header_rank", sa.Integer),
        sa.column("display_order", sa.Integer),
        sa.column("is_active", sa.Boolean),
    )
    # One default group every EXISTING category gets pointed at, so the
    # column can become NOT NULL below without inventing per-category
    # groupings this migration has no way to know. Staff can re-sort
    # categories into real groups afterward through the admin UI.
    uncategorized_id = uuid.uuid4()
    connection.execute(
        category_groups_table.insert().values(
            id=uncategorized_id,
            name="Uncategorized",
            icon=None,
            header_rank=None,
            display_order=0,
            is_active=True,
        )
    )

    categories_table = sa.table(
        "categories",
        sa.column("id", UUID),
        sa.column("category_group_id", UUID),
    )
    connection.execute(
        categories_table.update()
        .where(categories_table.c.category_group_id.is_(None))
        .values(category_group_id=uncategorized_id)
    )

    op.alter_column('categories', 'category_group_id', nullable=False)
    op.create_foreign_key('fk_categories_category_group_id', 'categories', 'category_groups', ['category_group_id'], ['id'])

    # --- product_discounts.promotion_id (nullable - grandfathers existing rows) ---
    # Pre-library discounts (created via POST /products/{id}/discounts
    # before this migration) are valid discounts in their own right and
    # are NOT retroactively linked to a library entry - see the model's
    # own comment on why this stays nullable rather than being backfilled.
    op.add_column('product_discounts', sa.Column('promotion_id', sa.UUID(), nullable=True))
    op.create_foreign_key('fk_product_discounts_promotion_id', 'product_discounts', 'promotions', ['promotion_id'], ['id'])

    # --- products: new marketing flags + promotion link ------------------------
    op.add_column('products', sa.Column('is_new_arrival', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('products', sa.Column('is_on_sale', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('products', sa.Column('applied_promotion_id', sa.UUID(), nullable=True))
    op.create_foreign_key('fk_products_applied_promotion_id', 'products', 'promotions', ['applied_promotion_id'], ['id'])

    # --- flag the one real branch as default ------------------------------------
    # "Kampala Main Branch" is the only branch with any real inventory
    # today (confirmed by querying the live DB before writing this
    # migration) - a name match, not a hardcoded id, so this behaves
    # sanely against a DIFFERENT database too (e.g. a fresh clone's empty
    # local DB): if no branch with this exact name exists, this simply
    # updates zero rows instead of erroring, leaving is_default unset
    # until an admin flags one via PATCH /branches/{id}/set-default.
    branches_table = sa.table(
        "branches",
        sa.column("name", sa.String),
        sa.column("is_default", sa.Boolean),
    )
    connection.execute(
        branches_table.update()
        .where(branches_table.c.name == "Kampala Main Branch")
        .values(is_default=True)
    )

    # --- seed the one store_settings row -----------------------------------------
    store_settings_table = sa.table(
        "store_settings",
        sa.column("id", UUID),
        sa.column("catalogue_filter_mode", sa.String),
    )
    connection.execute(
        store_settings_table.insert().values(
            id=uuid.uuid4(),
            catalogue_filter_mode="categories",
        )
    )

    # --- seed the 3 default homepage sections -------------------------------------
    homepage_sections_table = sa.table(
        "homepage_sections",
        sa.column("id", UUID),
        sa.column("title", sa.String),
        sa.column("description", sa.String),
        sa.column("section_type", sa.String),
        sa.column("category_id", UUID),
        sa.column("display_order", sa.Integer),
        sa.column("is_enabled", sa.Boolean),
        sa.column("max_products", sa.Integer),
        sa.column("view_all_href", sa.String),
    )
    for display_order, (title, section_type) in enumerate([
        ("Featured Products", "featured"),
        ("On Sale", "on_sale"),
        ("New Arrivals", "new_arrival"),
    ]):
        connection.execute(
            homepage_sections_table.insert().values(
                id=uuid.uuid4(),
                title=title,
                description=None,
                section_type=section_type,
                category_id=None,
                display_order=display_order,
                is_enabled=True,
                max_products=None,
                view_all_href=None,
            )
        )


def downgrade() -> None:
    op.drop_constraint('fk_products_applied_promotion_id', 'products', type_='foreignkey')
    op.drop_column('products', 'applied_promotion_id')
    op.drop_column('products', 'is_on_sale')
    op.drop_column('products', 'is_new_arrival')
    op.drop_constraint('fk_product_discounts_promotion_id', 'product_discounts', type_='foreignkey')
    op.drop_column('product_discounts', 'promotion_id')
    op.drop_constraint('fk_categories_category_group_id', 'categories', type_='foreignkey')
    op.drop_column('categories', 'category_group_id')
    op.drop_index('uq_branches_single_default', table_name='branches', postgresql_where=sa.text('is_default = true'))
    op.drop_column('branches', 'is_default')
    op.drop_table('homepage_sections')
    op.drop_table('store_settings')
    op.drop_table('promotions')
    op.drop_table('category_groups')
