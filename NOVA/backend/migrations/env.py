"""Alembic environment for the NOVA gameplay database.

The URL comes from the application settings, not from ``alembic.ini``:

    alembic upgrade head                                   # NOVA_DATABASE_URL
    alembic -x url=sqlite+aiosqlite:///./tmp.db upgrade head   # explicit override

Async drivers (``sqlite+aiosqlite``, ``postgresql+asyncpg``) are used directly,
so the same URL works for the app and for migrations. ``render_as_batch`` is on
because SQLite cannot ALTER most things in place.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import models  # noqa: F401  (registers every table on the metadata)
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """Explicit ``-x url=`` > ``alembic.ini`` > NOVA settings."""
    overrides = context.get_x_argument(as_dictionary=True)
    return (
        overrides.get("url")
        or config.get_main_option("sqlalchemy.url")
        or get_settings().database_url
    )


def _configure(connection: Connection | None, *, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,
        include_schemas=False,
        version_table="alembic_version",
        literal_binds=connection is None,
        dialect_opts={"paramstyle": "named"} if connection is None else {},
    )


def run_migrations_offline() -> None:
    """Emit SQL without connecting (`alembic upgrade head --sql`)."""
    _configure(None, url=_database_url())
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_database_url(), future=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_sync)
            await connection.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_async())
