"""Alembic environment for the local SQLite database."""

from alembic import context
from sqlalchemy import engine_from_config, pool
from imaginarium_forge.infrastructure.db.models.orm import Base

config = context.config


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        render_as_batch=True,
        target_metadata=Base.metadata,
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
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        connection.exec_driver_sql("PRAGMA busy_timeout = 5000")
        connection.commit()
        context.configure(
            connection=connection, render_as_batch=True, target_metadata=Base.metadata
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
