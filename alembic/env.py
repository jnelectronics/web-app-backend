from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.base import Base

# Import every module's models so Base.metadata is fully populated for autogenerate.
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.users import models as users_models  # noqa: F401
from app.modules.branches import models as branches_models  # noqa: F401
from app.modules.categories import models as categories_models  # noqa: F401
from app.modules.products import models as products_models  # noqa: F401
from app.modules.inventory import models as inventory_models  # noqa: F401
from app.modules.cart import models as cart_models  # noqa: F401
from app.modules.orders import models as orders_models  # noqa: F401
from app.modules.payments import models as payments_models  # noqa: F401
from app.modules.promotions import models as promotions_models  # noqa: F401
from app.modules.audit import models as audit_models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
