"""Tests for the Playwright trace.zip network parser."""

import base64
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from app.shadow.trace_parser import (
    InvalidTraceArchiveError,
    PlaywrightTraceParser,
    TraceParseError,
)


# --- fixtures / helpers ------------------------------------------------------


def _resource_snapshot(
    *,
    method: str = "GET",
    url: str = "https://api.example.com/items",
    request_headers: list[dict] | None = None,
    post_data: dict | None = None,
    status: int = 200,
    response_headers: list[dict] | None = None,
    content: dict | None = None,
    started_at: str | None = "2026-07-12T10:00:00.000Z",
    time_ms: float | None = 12.5,
    monotonic: float | None = 1000.0,
) -> dict:
    """Build a single ``resource-snapshot`` trace event (a HAR entry)."""
    snapshot: dict = {
        "request": {
            "method": method,
            "url": url,
            "headers": request_headers if request_headers is not None else [],
        },
        "response": {
            "status": status,
            "headers": response_headers if response_headers is not None else [],
            "content": content if content is not None else {},
        },
    }
    if post_data is not None:
        snapshot["request"]["postData"] = post_data
    if started_at is not None:
        snapshot["startedDateTime"] = started_at
    if time_ms is not None:
        snapshot["time"] = time_ms
    if monotonic is not None:
        snapshot["_monotonicTime"] = monotonic
    return {"type": "resource-snapshot", "snapshot": snapshot}


def _write_trace_zip(
    path: Path,
    *,
    network_lines: list[dict] | None = None,
    trace_lines: list[dict] | None = None,
    resources: dict[str, bytes] | None = None,
    extra_files: dict[str, bytes] | None = None,
) -> Path:
    """Write a synthetic Playwright trace.zip and return its path."""
    with zipfile.ZipFile(path, "w") as archive:
        if network_lines is not None:
            archive.writestr("trace.network", "\n".join(json.dumps(line) for line in network_lines))
        if trace_lines is not None:
            archive.writestr("trace.trace", "\n".join(json.dumps(line) for line in trace_lines))
        for sha1, body in (resources or {}).items():
            archive.writestr(f"resources/{sha1}", body)
        for name, body in (extra_files or {}).items():
            archive.writestr(name, body)
    return path


def _sha1(body: bytes) -> str:
    return hashlib.sha1(body).hexdigest()


# --- happy path --------------------------------------------------------------


def test_parses_request_and_response_pair(tmp_path):
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        network_lines=[
            _resource_snapshot(
                method="POST",
                url="https://api.example.com/submit",
                request_headers=[{"name": "Accept", "value": "application/json"}],
                post_data={"mimeType": "application/json", "text": '{"a":1}'},
                status=201,
                response_headers=[{"name": "Content-Type", "value": "application/json"}],
                content={"text": '{"ok":true}', "mimeType": "application/json"},
            )
        ],
    )

    snapshots = PlaywrightTraceParser().parse(trace)

    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.request.method == "POST"
    assert snap.request.url == "https://api.example.com/submit"

    # Updated: test tuple list representation & backward-compat dictionary
    assert snap.request.headers == [("Accept", "application/json")]
    assert snap.request.headers_dict == {"Accept": "application/json"}

    assert snap.request.body == '{"a":1}'
    assert snap.response.status == 201

    # Updated: test tuple list representation & backward-compat dictionary
    assert snap.response.headers == [("Content-Type", "application/json")]
    assert snap.response.headers_dict == {"Content-Type": "application/json"}

    assert snap.response.body == '{"ok":true}'
    assert snap.response.is_base64 is False
    assert snap.sequence == 0
    assert snap.duration_ms == 12.5
    assert snap.started_at is not None


def test_resolves_response_body_from_resources_by_sha1(tmp_path):
    body = b'{"resolved":true}'
    sha1 = _sha1(body)
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        network_lines=[_resource_snapshot(content={"_sha1": sha1, "mimeType": "application/json"})],
        resources={sha1: body},
    )

    snapshots = PlaywrightTraceParser().parse(trace)

    assert snapshots[0].response.body == '{"resolved":true}'
    assert snapshots[0].response.is_base64 is False


def test_binary_response_body_is_base64_encoded(tmp_path):
    body = b"\x89PNG\r\n\x1a\n\x00\xff"  # non-utf-8 bytes
    sha1 = _sha1(body)
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        network_lines=[_resource_snapshot(content={"_sha1": sha1, "mimeType": "image/png"})],
        resources={sha1: body},
    )

    snapshots = PlaywrightTraceParser().parse(trace)

    assert snapshots[0].response.is_base64 is True
    assert snapshots[0].response.body is not None
    assert base64.b64decode(snapshots[0].response.body) == body


def test_missing_resource_leaves_body_none(tmp_path):
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        network_lines=[_resource_snapshot(content={"_sha1": "deadbeef", "mimeType": "text/plain"})],
    )

    snapshots = PlaywrightTraceParser().parse(trace)

    assert snapshots[0].response.body is None


# --- ordering & pairing ------------------------------------------------------


def test_events_ordered_by_monotonic_time_with_sequence(tmp_path):
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        network_lines=[
            _resource_snapshot(url="https://api.example.com/c", monotonic=3000.0),
            _resource_snapshot(url="https://api.example.com/a", monotonic=1000.0),
            _resource_snapshot(url="https://api.example.com/b", monotonic=2000.0),
        ],
    )

    snapshots = PlaywrightTraceParser().parse(trace)

    assert [s.request.url for s in snapshots] == [
        "https://api.example.com/a",
        "https://api.example.com/b",
        "https://api.example.com/c",
    ]
    assert [s.sequence for s in snapshots] == [0, 1, 2]


# --- filtering non-network / incomplete --------------------------------------


def test_non_network_events_are_ignored(tmp_path):
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        network_lines=[
            {"type": "context-options", "options": {}},
            {"type": "frame-snapshot", "snapshot": {"foo": "bar"}},
            _resource_snapshot(url="https://api.example.com/real"),
            {"type": "action", "metadata": {"method": "click"}},
        ],
    )

    snapshots = PlaywrightTraceParser().parse(trace)

    assert len(snapshots) == 1
    assert snapshots[0].request.url == "https://api.example.com/real"


def test_pending_request_without_response_is_skipped(tmp_path):
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        network_lines=[
            _resource_snapshot(url="https://api.example.com/pending", status=0),
            _resource_snapshot(url="https://api.example.com/ok", status=200),
        ],
    )

    snapshots = PlaywrightTraceParser().parse(trace)

    assert [s.request.url for s in snapshots] == ["https://api.example.com/ok"]


def test_entry_without_request_is_skipped(tmp_path):
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        network_lines=[{"type": "resource-snapshot", "snapshot": {"response": {"status": 200}}}],
    )

    assert PlaywrightTraceParser().parse(trace) == []


def test_malformed_lines_are_skipped_but_valid_ones_parsed(tmp_path):
    with zipfile.ZipFile(tmp_path / "trace.zip", "w") as archive:
        lines = [
            "{not valid json",
            json.dumps(_resource_snapshot(url="https://api.example.com/ok")),
            "",
            "garbage",
        ]
        archive.writestr("trace.network", "\n".join(lines))

    snapshots = PlaywrightTraceParser().parse(tmp_path / "trace.zip")

    assert len(snapshots) == 1
    assert snapshots[0].request.url == "https://api.example.com/ok"


# --- stream selection --------------------------------------------------------


def test_prefers_network_stream_over_trace_stream(tmp_path):
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        network_lines=[_resource_snapshot(url="https://api.example.com/from-network")],
        trace_lines=[_resource_snapshot(url="https://api.example.com/from-trace")],
    )

    snapshots = PlaywrightTraceParser().parse(trace)

    assert [s.request.url for s in snapshots] == ["https://api.example.com/from-network"]


def test_falls_back_to_trace_stream_when_no_network_stream(tmp_path):
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        trace_lines=[_resource_snapshot(url="https://api.example.com/from-trace")],
    )

    snapshots = PlaywrightTraceParser().parse(trace)

    assert [s.request.url for s in snapshots] == ["https://api.example.com/from-trace"]


def test_valid_trace_with_no_network_events_returns_empty(tmp_path):
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        network_lines=[{"type": "context-options", "options": {}}],
    )

    assert PlaywrightTraceParser().parse(trace) == []


# --- graceful failure --------------------------------------------------------


def test_missing_archive_raises(tmp_path):
    with pytest.raises(InvalidTraceArchiveError):
        PlaywrightTraceParser().parse(tmp_path / "nope.zip")


def test_directory_path_raises(tmp_path):
    with pytest.raises(InvalidTraceArchiveError):
        PlaywrightTraceParser().parse(tmp_path)


def test_non_zip_file_raises(tmp_path):
    bogus = tmp_path / "trace.zip"
    bogus.write_bytes(b"this is definitely not a zip archive")

    with pytest.raises(InvalidTraceArchiveError):
        PlaywrightTraceParser().parse(bogus)


def test_archive_without_trace_streams_raises(tmp_path):
    trace = _write_trace_zip(
        tmp_path / "trace.zip",
        extra_files={"README.txt": b"no trace here"},
    )

    with pytest.raises(InvalidTraceArchiveError):
        PlaywrightTraceParser().parse(trace)


def test_errors_derive_from_trace_parse_error():
    assert issubclass(InvalidTraceArchiveError, TraceParseError)
