"""Alembic migrations must reproduce exactly what the models declare.

Dev boots with ``NOVA_AUTO_CREATE_TABLES=true`` (``create_all``), production runs
``alembic upgrade head``. If those two paths ever diverge, prod is missing a
column that the code writes to – so the suite compares them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.db import models  # noqa: F401  (register the tables)
from app.db.base import Base
from app.main import create_app
from conftest import make_settings

ROOT = Path(__file__).resolve().parent.parent


def _config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def migrated(tmp_path: Path) -> Path:
    """A database built by Alembic only – never by ``create_all``."""
    path = tmp_path / "migrated.db"
    command.upgrade(_config(f"sqlite+aiosqlite:///{path}"), "head")
    return path


def test_upgrade_head_creates_every_table_and_column(migrated: Path) -> None:
    inspector = inspect(create_engine(f"sqlite:///{migrated}"))
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
    for name, table in Base.metadata.tables.items():
        assert {column["name"] for column in inspector.get_columns(name)} == {
            column.name for column in table.columns
        }, name


def test_migrations_are_in_sync_with_the_models(migrated: Path) -> None:
    """No pending autogenerate diff: a model change needs a new revision."""
    engine = create_engine(f"sqlite:///{migrated}")
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "render_as_batch": True}
        )
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert not diff, f"run `alembic revision --autogenerate`: {diff}"


def test_the_app_runs_on_a_migrated_database(migrated: Path, tmp_path: Path) -> None:
    """The real proof: play a turn with auto_create_tables disabled."""
    settings = make_settings(
        tmp_path,
        database_url=f"sqlite+aiosqlite:///{migrated}",
        auto_create_tables=False,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/ready").json()["database"] is True
        created = client.post("/api/v1/games", json={"player_name": "Migrated"})
        assert created.status_code == 201, created.text
        game_id = created.json()["game_id"]
        assert (
            client.post(f"/api/v1/games/{game_id}/tick", json={"dt_seconds": 1.0}).status_code
            == 200
        )
        assert client.get(f"/api/v1/games/{game_id}/events").json()["events"]


def test_downgrade_base_leaves_an_empty_database(migrated: Path) -> None:
    command.downgrade(_config(f"sqlite+aiosqlite:///{migrated}"), "base")
    inspector = inspect(create_engine(f"sqlite:///{migrated}"))
    assert set(inspector.get_table_names()) <= {"alembic_version"}
