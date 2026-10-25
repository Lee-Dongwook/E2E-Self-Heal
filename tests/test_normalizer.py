"""Unit tests for app.shadow.normalizer.RequestNormalizer."""

from typing import Any

import pytest

from app.shadow.match_options import MatchOptions
from app.shadow.normalizer import RequestNormalizer


@pytest.fixture
def normalizer() -> RequestNormalizer:
    return RequestNormalizer()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("id-550e8400-e29b-41d4-a716-446655440000-end", "id-<UUID>-end"),
        ("created at 2024-01-01T12:00:00Z", "created at <TIMESTAMP>"),
        ("created at 2024-01-01T12:00:00+05:30", "created at <TIMESTAMP>"),
        ("ts=1718000000000 done", "ts=<TIMESTAMP> done"),
        (
            "req 550e8400-e29b-41d4-a716-446655440000 at 2024-01-01T12:00:00Z",
            "req <UUID> at <TIMESTAMP>",
        ),
        ("hello world", "hello world"),
        (123, "123"),
        (True, "True"),
    ],
    ids=[
        "uuid",
        "iso-timestamp",
        "timestamp-offset",
        "epoch",
        "multiple",
        "plain",
        "integer",
        "bool",
    ],
)
def test_normalize_value_replaces_dynamic_values(
    normalizer: RequestNormalizer, value: Any, expected: str
) -> None:
    assert normalizer.normalize_value(value) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "http://example.com/api/users/550e8400-e29b-41d4-a716-446655440000",
            "/api/users/<UUID>",
        ),
        ("http://example.com/api/users/list", "/api/users/list"),
        (
            "/api/users/123e4567-e89b-12d3-a456-426614174000",
            "/api/users/<UUID>",
        ),
    ],
    ids=["dynamic-path", "static-path", "another-dynamic-path"],
)
def test_normalize_url_normalizes_paths(
    normalizer: RequestNormalizer, url: str, expected: str
) -> None:
    path, _ = normalizer.normalize_url(url)
    assert path == expected


@pytest.mark.parametrize(
    ("url", "options", "expected"),
    [
        ("/api/x?a=1&b=2", None, {"a": ["1"], "b": ["2"]}),
        ("/api/x?a=1&timestamp=999&nonce=abc&sig=xyz", None, {"a": ["1"]}),
        ("/api/x?Timestamp=999&a=1", None, {"a": ["1"]}),
        (
            "/api/x?Tab=all",
            MatchOptions(case_insensitive_query_keys=True),
            {"tab": ["all"]},
        ),
        (
            "/api/x?req_id=550e8400-e29b-41d4-a716-446655440000",
            None,
            {"req_id": ["<UUID>"]},
        ),
        ("/api/x", None, {}),
        ("/api/x?tag=a&tag=b", None, {"tag": ["a", "b"]}),
        (
            "/api/x?a=1&debug=volatile",
            MatchOptions(ignored_query_params=frozenset({"debug"})),
            {"a": ["1"]},
        ),
    ],
    ids=[
        "static",
        "dynamic-keys",
        "dynamic-key-case",
        "case-insensitive",
        "dynamic-value",
        "empty",
        "repeated",
        "ignored",
    ],
)
def test_normalize_url_normalizes_queries(
    url: str, options: MatchOptions | None, expected: dict[str, list[str]]
) -> None:
    path, query = RequestNormalizer(options).normalize_url(url)
    assert path == "/api/x"
    assert query == expected


def test_normalize_url_query_param_order_does_not_affect_result(
    normalizer: RequestNormalizer,
) -> None:
    _, query1 = normalizer.normalize_url("/api/x?a=1&b=2")
    _, query2 = normalizer.normalize_url("/api/x?b=2&a=1")
    assert query1 == query2


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Content-Type": "application/json"}, {"content-type": "application/json"}),
        (
            {"Authorization": "Bearer secret", "Content-Type": "application/json", "Cookie": "x=y"},
            {"content-type": "application/json"},
        ),
        (
            {"X-Request-Id": "550e8400-e29b-41d4-a716-446655440000"},
            {"x-request-id": "<UUID>"},
        ),
        ({}, {}),
        (
            {"Accept": "application/json", "X-Custom": "value"},
            {"accept": "application/json", "x-custom": "value"},
        ),
    ],
    ids=["lowercase", "dynamic", "dynamic-value", "empty", "stable"],
)
def test_normalize_headers_filters_and_normalizes(
    normalizer: RequestNormalizer, headers: dict[str, str], expected: dict[str, str]
) -> None:
    assert normalizer.normalize_headers(headers) == expected


def test_normalize_headers_honors_extra_ignored_headers() -> None:
    normalizer = RequestNormalizer(MatchOptions(ignored_headers=frozenset({"X-Request-Id"})))
    assert normalizer.normalize_headers({"X-Request-Id": "req-123", "Accept": "*/*"}) == {
        "accept": "*/*"
    }


@pytest.mark.parametrize("body", [None, ""], ids=["none", "empty"])
def test_normalize_body_empty_values(normalizer: RequestNormalizer, body: str | None) -> None:
    assert normalizer.normalize_body(body) == ""


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            '{"x": 1, "req_id": "550e8400-e29b-41d4-a716-446655440000"}',
            {"x": 1, "req_id": "<UUID>"},
        ),
        ('{"session_id": "abc123", "x": 1}', {"session_id": "<DYNAMIC>", "x": 1}),
        ('{"Token": "abc123", "x": 1}', {"Token": "<DYNAMIC>", "x": 1}),
        (
            '{"outer": {"session_id": "abc", "y": 2}}',
            {"outer": {"session_id": "<DYNAMIC>", "y": 2}},
        ),
        (
            '{"items": [{"x": 1}, {"session_id": "abc"}]}',
            {"items": [{"x": 1}, {"session_id": "<DYNAMIC>"}]},
        ),
        ('{"created_at": "2024-01-01T12:00:00Z"}', {"created_at": "<TIMESTAMP>"}),
        (
            '{"count": 5, "active": true, "ratio": 1.5, "missing": null}',
            {"count": 5, "active": True, "ratio": 1.5, "missing": None},
        ),
    ],
    ids=[
        "json-values",
        "dynamic-key",
        "dynamic-key-case",
        "nested-object",
        "list",
        "string-value",
        "scalar-values",
    ],
)
def test_normalize_body_normalizes_json(
    normalizer: RequestNormalizer, body: str, expected: Any
) -> None:
    assert normalizer.normalize_body(body) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "user 550e8400-e29b-41d4-a716-446655440000 logged in",
            "user <UUID> logged in",
        ),
        ("event at 2024-01-01T12:00:00Z", "event at <TIMESTAMP>"),
    ],
    ids=["uuid", "timestamp"],
)
def test_normalize_body_scrubs_non_json_text(
    normalizer: RequestNormalizer, body: str, expected: str
) -> None:
    assert normalizer.normalize_body(body) == expected


def test_order_insensitive_arrays_are_canonicalized() -> None:
    body = '{"tags": ["c", "a", "b"]}'
    assert RequestNormalizer().normalize_body(body)["tags"] == ["c", "a", "b"]
    assert RequestNormalizer(MatchOptions(order_insensitive_arrays=True)).normalize_body(body)[
        "tags"
    ] == [
        "a",
        "b",
        "c",
    ]
