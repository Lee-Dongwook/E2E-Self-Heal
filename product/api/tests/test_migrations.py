"""The Alembic migrations apply and roll back cleanly on a real database (#340)."""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from product_api.config import get_settings

# Postgres, not SQLite: the migrations use Postgres-only types (JSONB in create_projects_and_runs),
# so SQLite cannot run them at all. The `product` workflow provides a Postgres service container.
TEST_DATABASE_URL = os.getenv("PRODUCT_API_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set PRODUCT_API_TEST_DATABASE_URL to run Postgres integration tests",
)

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


@pytest.fixture
def scratch_database_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A throwaway database on the test server, so migrations never touch the shared one.

    The ingest tests use the database the workflow migrated before pytest; downgrading that
    one would drop their tables.
    """
    assert TEST_DATABASE_URL is not None
    server = make_url(TEST_DATABASE_URL)
    name = f"migrations_{uuid.uuid4().hex[:12]}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    url = server.set(database=name).render_as_string(hide_password=False)
    # migrations/env.py reads the URL through the lru_cached get_settings().
    monkeypatch.setenv("PRODUCT_API_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        yield url
    finally:
        get_settings.cache_clear()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _current_revision(url: str) -> str | None:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


def test_migrations_upgrade_to_head_and_downgrade_to_base(scratch_database_url: str) -> None:
    config = Config(str(ALEMBIC_INI))
    head = ScriptDirectory.from_config(config).get_current_head()

    command.upgrade(config, "head")

    assert _current_revision(scratch_database_url) == head
    assert {"projects", "runs"} <= _tables(scratch_database_url)

    command.downgrade(config, "base")

    assert _current_revision(scratch_database_url) is None
    assert _tables(scratch_database_url) <= {"alembic_version"}
