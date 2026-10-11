"""Postgres integration tests for CI result ingestion."""

import os
from collections.abc import Iterator
from typing import Any

import pytest
from app.schemas import SCHEMA_VERSION
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from product_api.db import get_engine
from product_api.main import create_app
from product_api.oidc import UploadIdentity, require_upload_auth

TEST_DATABASE_URL = os.getenv("PRODUCT_API_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="set PRODUCT_API_TEST_DATABASE_URL to run Postgres integration tests",
)


@pytest.fixture
def engine() -> Iterator[Engine]:
    assert TEST_DATABASE_URL is not None
    database = create_engine(TEST_DATABASE_URL)
    with database.begin() as connection:
        connection.execute(text("TRUNCATE runs, projects RESTART IDENTITY CASCADE"))
    yield database
    database.dispose()


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    api = create_app()
    api.dependency_overrides[get_engine] = lambda: engine
    api.dependency_overrides[require_upload_auth] = lambda: _identity()
    with TestClient(api) as test_client:
        yield test_client


def _identity() -> UploadIdentity:
    return UploadIdentity(123456, "acme/storefront", 98765, 1, "a" * 40, "refs/pull/42/merge")


def _request(payload: dict[str, Any], *, kind_offset: int = 0) -> dict[str, Any]:
    return {
        "repository": {"github_repo_id": 123456 + kind_offset, "full_name": "acme/storefront"},
        "workflow": {"run_id": 98765 + kind_offset, "run_attempt": 1},
        "commit_sha": "a" * 40,
        "ref": "refs/pull/42/merge",
        "payload": payload,
    }


@pytest.mark.parametrize(
    ("payload", "expected_success"),
    [
        (
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "repair",
                "test_script_path": "tests/login.spec.ts",
                "is_success": True,
                "loop_count": 1,
            },
            True,
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "refusal",
                "test_script_path": "tests/login.spec.ts",
                "reason": "ambiguous_target",
                "is_success": False,
                "loop_count": 1,
                "evidence": {},
            },
            False,
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "suite",
                "total_failed": 1,
                "healed": 1,
                "is_success": True,
                "results": [],
            },
            True,
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "review",
                "test_script_path": "tests/login.spec.ts",
                "findings": [],
                "has_findings": False,
                "is_complete": True,
            },
            True,
        ),
    ],
)
def test_captured_core_payloads_round_trip_unchanged(
    client: TestClient, engine: Engine, payload: dict[str, Any], expected_success: bool
) -> None:
    response = client.post("/v1/runs", json=_request(payload, kind_offset=len(payload["kind"])))

    assert response.status_code == 201
    run_id = response.json()["id"]
    with engine.connect() as connection:
        stored = (
            connection.execute(
                text("SELECT payload, is_success FROM runs WHERE id = :id"), {"id": run_id}
            )
            .mappings()
            .one()
        )
    assert stored["payload"] == payload
    assert stored["is_success"] is expected_success


def test_repeat_upload_is_idempotent(client: TestClient, engine: Engine) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "repair",
        "test_script_path": "tests/login.spec.ts",
        "is_success": True,
        "loop_count": 1,
    }
    request = _request(payload)

    first = client.post("/v1/runs", json=request)
    repeated = client.post("/v1/runs", json=request)

    assert first.status_code == 201
    assert repeated.status_code == 201
    assert first.json() == {"id": first.json()["id"], "created": True}
    assert repeated.json() == {"id": first.json()["id"], "created": False}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM runs")).scalar_one() == 1


def test_unknown_schema_version_names_the_supported_versions(client: TestClient) -> None:
    payload = {
        "schema_version": "99.0",
        "kind": "repair",
        "test_script_path": "tests/login.spec.ts",
        "is_success": True,
        "loop_count": 1,
    }

    response = client.post("/v1/runs", json=_request(payload))

    assert response.status_code == 422
    assert f"supported versions: {SCHEMA_VERSION}" in response.text


def test_upload_has_a_body_size_limit(client: TestClient) -> None:
    response = client.post("/v1/runs", content=b"{}", headers={"content-length": "1048577"})

    assert response.status_code == 413
