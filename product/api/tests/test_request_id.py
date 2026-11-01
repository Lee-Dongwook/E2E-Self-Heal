"""Every request gets a request id, on the response and on its log events (#341)."""

import re
from collections.abc import Iterator

import pytest
import structlog
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from product_api.config import get_settings
from product_api.db import get_engine
from product_api.main import REQUEST_ID_HEADER, create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def captured() -> Iterator[list[structlog.typing.EventDict]]:
    # merge_contextvars, as in the app's logging configuration, so bound values are visible.
    with structlog.testing.capture_logs(
        processors=[structlog.contextvars.merge_contextvars]
    ) as logs:
        yield logs


def test_a_request_without_an_id_gets_a_generated_one(client: TestClient) -> None:
    response = client.get("/version")

    assert response.status_code == 200
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers[REQUEST_ID_HEADER])


def test_an_incoming_request_id_is_echoed_back_unchanged(client: TestClient) -> None:
    response = client.get("/version", headers={REQUEST_ID_HEADER: "ci-run-42/Attempt_1"})

    assert response.headers[REQUEST_ID_HEADER] == "ci-run-42/Attempt_1"


def test_each_request_logs_one_finished_event_with_its_id(
    client: TestClient, captured: list[structlog.typing.EventDict]
) -> None:
    response = client.get("/version", headers={REQUEST_ID_HEADER: "abc123"})

    (finished,) = [event for event in captured if event["event"] == "request_finished"]
    assert finished["request_id"] == "abc123"
    assert finished["method"] == "GET"
    assert finished["path"] == "/version"
    assert finished["status_code"] == response.status_code == 200
    assert finished["duration_ms"] >= 0


def test_events_logged_inside_an_endpoint_carry_the_request_id(
    captured: list[structlog.typing.EventDict],
) -> None:
    # /healthz runs in FastAPI's thread pool; an unreachable database makes it log an error.
    api = create_app()
    api.dependency_overrides[get_engine] = lambda: create_engine(
        "postgresql+psycopg://u:p@127.0.0.1:1/db"
    )

    response = TestClient(api).get("/healthz", headers={REQUEST_ID_HEADER: "trace-me"})

    assert response.status_code == 503
    (unreachable,) = [event for event in captured if event["event"] == "database_unreachable"]
    assert unreachable["request_id"] == "trace-me"


def test_a_response_returned_before_the_endpoint_still_has_a_request_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The upload-size check answers 413 without reaching the endpoint or the database.
    monkeypatch.setenv("PRODUCT_API_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("PRODUCT_API_MAX_UPLOAD_BYTES", "10")
    get_settings.cache_clear()
    try:
        response = client.post("/v1/runs", content=b"x" * 11)
    finally:
        get_settings.cache_clear()

    assert response.status_code == 413
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers[REQUEST_ID_HEADER])


def test_an_unhandled_error_still_returns_a_request_id_and_logs_a_500(
    captured: list[structlog.typing.EventDict],
) -> None:
    # The requests most worth tracing are the ones that fail.
    def broken_engine() -> None:
        raise RuntimeError("engine construction failed")

    api = create_app()
    api.dependency_overrides[get_engine] = broken_engine
    client = TestClient(api, raise_server_exceptions=False)

    response = client.get("/healthz", headers={REQUEST_ID_HEADER: "boom-1"})

    assert response.status_code == 500
    assert response.headers[REQUEST_ID_HEADER] == "boom-1"
    (failed,) = [event for event in captured if event["event"] == "request_failed"]
    assert failed["request_id"] == "boom-1"
    (finished,) = [event for event in captured if event["event"] == "request_finished"]
    assert finished["status_code"] == 500
    assert finished["request_id"] == "boom-1"
