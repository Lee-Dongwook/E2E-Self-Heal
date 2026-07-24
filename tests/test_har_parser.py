"""Tests for the standalone HAR trace parser."""

import json
from pathlib import Path

import pytest

from app.shadow.har_parser import HarTraceParser, InvalidHarFileError
from app.shadow.interfaces import ITraceParser
from app.shadow.trace_parser import TraceParseError

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample.har"


def _entry(
    url: str, *, started_at: str | None = None, post_data: dict | None = None
) -> dict[str, object]:
    request: dict[str, object] = {
        "method": "POST" if post_data else "GET",
        "url": url,
        "headers": [],
    }
    if post_data is not None:
        request["postData"] = post_data

    entry: dict[str, object] = {
        "request": request,
        "response": {"status": 200, "headers": [], "content": {"text": "ok"}},
    }
    if started_at is not None:
        entry["startedDateTime"] = started_at
    return entry


def _write_har(path: Path, entries: list[dict]) -> Path:
    path.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
    return path


def test_implements_trace_parser_contract() -> None:
    assert isinstance(HarTraceParser(), ITraceParser)


def test_parses_standalone_har_fixture() -> None:
    snapshots = HarTraceParser().parse(FIXTURE_PATH)

    assert len(snapshots) == 2
    first, second = snapshots

    assert first.request.method == "POST"
    assert first.request.url == "https://api.example.com/items"
    assert first.request.headers == {"Accept": "application/json"}
    assert first.request.body == '{"name":"widget"}'
    assert first.response.status == 201
    assert first.response.headers == {"Content-Type": "application/json"}
    assert first.response.body == '{"id":42}'
    assert first.response.is_base64 is False
    assert first.sequence == 0
    assert first.duration_ms == 15.5
    assert first.started_at is not None

    assert second.request.method == "GET"
    assert second.response.body == "iVBORw0KGgo="
    assert second.response.is_base64 is True
    assert second.sequence == 1
    assert second.duration_ms == 4.0


def test_skips_incomplete_entries_and_keeps_sequences_contiguous(tmp_path: Path) -> None:
    document = {
        "log": {
            "entries": [
                {"request": {"method": "GET", "url": "https://example.com/pending"}},
                {
                    "request": {"method": "GET", "url": "https://example.com/ok"},
                    "response": {"status": 200, "headers": [], "content": {"text": "ok"}},
                },
            ]
        }
    }
    path = tmp_path / "partial.har"
    path.write_text(json.dumps(document), encoding="utf-8")

    snapshots = HarTraceParser().parse(path)

    assert len(snapshots) == 1
    assert snapshots[0].request.url == "https://example.com/ok"
    assert snapshots[0].sequence == 0


def test_empty_entries_returns_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "empty.har"
    path.write_text('{"log":{"version":"1.2","entries":[]}}', encoding="utf-8")

    assert HarTraceParser().parse(path) == []


def test_accepts_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "bom.har"
    document = json.dumps({"log": {"entries": [_entry("https://example.com")]}})
    path.write_text(document, encoding="utf-8-sig")

    snapshots = HarTraceParser().parse(path)

    assert len(snapshots) == 1


def test_reconstructs_urlencoded_post_data_params(tmp_path: Path) -> None:
    path = _write_har(
        tmp_path / "form.har",
        [
            _entry(
                "https://example.com/form",
                post_data={
                    "mimeType": "application/x-www-form-urlencoded",
                    "params": [
                        {"name": "query", "value": "hello world"},
                        {"name": "tag", "value": "a&b"},
                    ],
                },
            )
        ],
    )

    snapshots = HarTraceParser().parse(path)

    assert snapshots[0].request.body == "query=hello+world&tag=a%26b"


def test_post_data_text_takes_precedence_over_params(tmp_path: Path) -> None:
    path = _write_har(
        tmp_path / "text.har",
        [
            _entry(
                "https://example.com/form",
                post_data={
                    "mimeType": "application/x-www-form-urlencoded",
                    "text": "raw=body",
                    "params": [{"name": "ignored", "value": "value"}],
                },
            )
        ],
    )

    snapshots = HarTraceParser().parse(path)

    assert snapshots[0].request.body == "raw=body"


def test_reconstructs_multipart_post_data_params(tmp_path: Path) -> None:
    path = _write_har(
        tmp_path / "multipart.har",
        [
            _entry(
                "https://example.com/upload",
                post_data={
                    "mimeType": "multipart/form-data; boundary=test-boundary",
                    "params": [
                        {"name": "description", "value": "example"},
                        {
                            "name": "upload",
                            "value": "file contents",
                            "fileName": "sample.txt",
                            "contentType": "text/plain",
                        },
                    ],
                },
            )
        ],
    )

    snapshots = HarTraceParser().parse(path)

    assert snapshots[0].request.body == (
        '--test-boundary\r\nContent-Disposition: form-data; name="description"'
        '\r\n\r\nexample\r\n--test-boundary\r\nContent-Disposition: form-data; name="upload"; '
        'filename="sample.txt"\r\nContent-Type: text/plain\r\n\r\nfile contents\r\n'
        "--test-boundary--\r\n"
    )


def test_orders_entries_chronologically_and_assigns_sequence(tmp_path: Path) -> None:
    path = _write_har(
        tmp_path / "ordered.har",
        [
            _entry("https://example.com/third", started_at="2026-07-24T12:00:03Z"),
            _entry("https://example.com/first", started_at="2026-07-24T12:00:01Z"),
            _entry("https://example.com/second", started_at="2026-07-24T12:00:02Z"),
        ],
    )

    snapshots = HarTraceParser().parse(path)

    assert [snapshot.request.url for snapshot in snapshots] == [
        "https://example.com/first",
        "https://example.com/second",
        "https://example.com/third",
    ]
    assert [snapshot.sequence for snapshot in snapshots] == [0, 1, 2]


def test_entries_without_valid_timestamps_sort_last_in_source_order(tmp_path: Path) -> None:
    path = _write_har(
        tmp_path / "fallback-order.har",
        [
            _entry("https://example.com/missing"),
            _entry("https://example.com/later", started_at="2026-07-24T12:00:02Z"),
            _entry("https://example.com/invalid", started_at="not-a-timestamp"),
            _entry("https://example.com/earlier", started_at="2026-07-24T12:00:01Z"),
        ],
    )

    snapshots = HarTraceParser().parse(path)

    assert [snapshot.request.url for snapshot in snapshots] == [
        "https://example.com/earlier",
        "https://example.com/later",
        "https://example.com/missing",
        "https://example.com/invalid",
    ]


def test_rejects_boolean_status_and_duration_values(tmp_path: Path) -> None:
    boolean_status = _entry("https://example.com/status")
    boolean_status["response"] = {
        "status": True,
        "headers": [],
        "content": {"text": "invalid"},
    }
    boolean_duration = _entry("https://example.com/duration")
    boolean_duration["time"] = True
    path = _write_har(tmp_path / "booleans.har", [boolean_status, boolean_duration])

    snapshots = HarTraceParser().parse(path)

    assert len(snapshots) == 1
    assert snapshots[0].request.url == "https://example.com/duration"
    assert snapshots[0].duration_ms is None


def test_timezone_less_timestamp_uses_stable_fallback_order(tmp_path: Path) -> None:
    path = _write_har(
        tmp_path / "naive-timestamp.har",
        [
            _entry("https://example.com/naive", started_at="2026-07-24T12:00:00"),
            _entry("https://example.com/aware", started_at="2026-07-24T13:00:00+00:00"),
        ],
    )

    snapshots = HarTraceParser().parse(path)

    assert [snapshot.request.url for snapshot in snapshots] == [
        "https://example.com/aware",
        "https://example.com/naive",
    ]
    assert snapshots[1].started_at is None


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"log": {}}',
        '{"log": {"entries": {}}}',
    ],
)
def test_invalid_har_document_raises(tmp_path: Path, content: str) -> None:
    path = tmp_path / "invalid.har"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(InvalidHarFileError):
        HarTraceParser().parse(path)


def test_missing_file_and_directory_raise(tmp_path: Path) -> None:
    with pytest.raises(InvalidHarFileError):
        HarTraceParser().parse(tmp_path / "missing.har")
    with pytest.raises(InvalidHarFileError):
        HarTraceParser().parse(tmp_path)


def test_invalid_utf8_raises_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "invalid-encoding.har"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(InvalidHarFileError):
        HarTraceParser().parse(path)


def test_error_derives_from_trace_parse_error() -> None:
    assert issubclass(InvalidHarFileError, TraceParseError)
