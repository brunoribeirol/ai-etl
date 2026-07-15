"""Alembic environment — migrates the application database (APP_DATABASE_URL).

Not `POSTGRES_URL`: that variable points at the pipeline's source/destination
Postgres, a database this project's migrations never touch.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from ai_etl.audit.models import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_app_database_url = os.getenv("APP_DATABASE_URL")
if not _app_database_url:
    raise EnvironmentError("APP_DATABASE_URL environment variable is not set.")
config.set_main_option("sqlalchemy.url", _app_database_url)

target_metadata = metadata


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
