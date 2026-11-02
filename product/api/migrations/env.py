from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from product_api.config import get_settings
from product_api.models import Base

config = context.config
# The URL comes from PRODUCT_API_DATABASE_URL, like the app; '%' is escaped for configparser.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The ORM models, so `alembic revision --autogenerate` can diff them against the database.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit the migration SQL to stdout from the URL alone, without a database connection."""
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
    """Run the migrations against the database at the configured URL."""
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
