import json
import stat

import pytest
from pydantic import SecretStr

import app.config as config_module
from app.shadow import (
    CapturedRequest,
    CapturedResponse,
    CookieSnapshot,
    LocalStorageSnapshot,
    NetworkSnapshot,
    ShadowConfig,
    ShadowSnapshot,
    ShadowWorkspace,
)
from app.shadow.har_parser import HarTraceParser
from app.shadow.redaction import (
    REDACTED,
    RedactionRecord,
    redact_text,
    redact_url,
    redact_value,
)
from app.shadow.snapshot_store import SnapshotStore


def test_redact_url_removes_sensitive_query_values() -> None:
    safe = redact_url("https://example.test/data?page=2&token=secret")
    assert "secret" not in safe
    assert "page=2" in safe
    assert "token=%5BREDACTED%5D" in safe


def test_redact_value_removes_unstructured_credentials_and_url_components() -> None:
    safe = redact_value(
        "Authorization: Bearer test-token; password=test-password "
        "https://user:test-password@example.test/path#test-token"
    )

    assert safe == (
        "Authorization: Bearer [REDACTED]; password=[REDACTED] https://example.test/path#[REDACTED]"
    )


def test_snapshot_store_redacts_secrets_and_uses_private_permissions(tmp_path) -> None:
    workspace = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path / "shadow")))
    store = SnapshotStore(workspace)
    snapshot = ShadowSnapshot(
        snapshot_id="safe",
        network_snapshots=[
            NetworkSnapshot(
                request=CapturedRequest(
                    method="POST",
                    url="https://example.test/login?token=secret",
                    headers={"Authorization": "Bearer secret"},
                    body='{"password":"secret","name":"Ada"}',
                ),
                response=CapturedResponse(
                    status=200,
                    headers={"Set-Cookie": "session=secret"},
                    body='{"access_token":"secret","name":"Ada"}',
                ),
            )
        ],
        state_snapshots=[
            LocalStorageSnapshot(origin="https://example.test", items={"token": "secret"}),
            CookieSnapshot(name="session", value="secret", domain="example.test"),
        ],
    )

    store.save_snapshot("safe", snapshot)
    path = store._get_snapshot_path("safe")
    content = path.read_text()

    assert "secret" not in content
    assert "Ada" in content
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(content)["snapshot_id"] == "safe"


# --- repeated header names (#226) ---------------------------------------------------------


def _har_with_duplicate_response_headers(tmp_path) -> list[NetworkSnapshot]:
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://example.test/login",
                        "headers": [],
                    },
                    "response": {
                        "status": 200,
                        "headers": [
                            {"name": "Set-Cookie", "value": "session=first-secret; Path=/"},
                            {"name": "Set-Cookie", "value": "csrf=second-secret; Path=/"},
                            {"name": "Link", "value": "<a>; rel=preload"},
                            {"name": "Link", "value": "<b>; rel=preload"},
                            {"name": "Vary", "value": "Accept"},
                            {"name": "Vary", "value": "Origin"},
                        ],
                        "content": {"text": "ok"},
                    },
                }
            ]
        }
    }
    path = tmp_path / "duplicates.har"
    path.write_text(json.dumps(har), encoding="utf-8")
    return HarTraceParser().parse(path)


def test_duplicate_headers_survive_a_save_and_load_round_trip(tmp_path) -> None:
    store = SnapshotStore(ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path / "shadow"))))
    snapshot = ShadowSnapshot(
        snapshot_id="dupes", network_snapshots=_har_with_duplicate_response_headers(tmp_path)
    )

    store.save_snapshot("dupes", snapshot)
    headers = store.get_snapshot("dupes").network_snapshots[0].response.headers

    assert headers["Link"] == "<a>; rel=preload, <b>; rel=preload"
    assert headers["Vary"] == "Accept, Origin"


def test_redaction_removes_every_cookie_from_a_folded_set_cookie_header(tmp_path) -> None:
    store = SnapshotStore(ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path / "shadow"))))
    snapshot = ShadowSnapshot(
        snapshot_id="dupes", network_snapshots=_har_with_duplicate_response_headers(tmp_path)
    )

    store.save_snapshot("dupes", snapshot)
    content = store._get_snapshot_path("dupes").read_text()

    # The folded value is one header entry, so neither cookie can slip through.
    assert "first-secret" not in content
    assert "second-secret" not in content
    assert store.get_snapshot("dupes").network_snapshots[0].response.headers["Set-Cookie"] == (
        "[REDACTED]"
    )


# --- free-text boundary (#377) ------------------------------------------------------------


def _configure_sensitive(
    monkeypatch: pytest.MonkeyPatch, *, env_var: str = "", literal: str = ""
) -> None:
    """Name secrets through configuration, as a user would in .env."""
    monkeypatch.setattr(config_module.settings, "sensitive_env_vars", env_var)
    monkeypatch.setattr(config_module.settings, "sensitive_values", SecretStr(literal))


def test_redact_text_covers_a_bare_log_line() -> None:
    # Prompts are built from bare strings (a Playwright log, an ARIA snapshot), not only from
    # structures, so the same credential patterns must apply without a dict or list around them.
    assert redact_text("Authorization: Bearer abc123; password=hunter2") == (
        "Authorization: Bearer [REDACTED]; password=[REDACTED]"
    )


def test_a_configured_environment_variable_value_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_TEST_SESSION_SECRET", "session-value-42")
    _configure_sensitive(monkeypatch, env_var="E2E_TEST_SESSION_SECRET")
    record = RedactionRecord()

    safe = redact_text("login used session-value-42, then repeated session-value-42", record=record)

    assert safe == "login used [REDACTED], then repeated [REDACTED]"
    assert record.count == 2


def test_a_named_but_unset_environment_variable_redacts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Redaction must not fail (or redact an empty string everywhere) when the variable is not
    # in the environment of the process that runs the heal.
    monkeypatch.delenv("E2E_TEST_ABSENT_SECRET", raising=False)
    _configure_sensitive(monkeypatch, env_var="E2E_TEST_ABSENT_SECRET")

    assert redact_text("nothing to hide") == "nothing to hide"


def test_a_configured_literal_value_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_sensitive(monkeypatch, literal="tenant-abc-123")

    assert redact_text("tenant tenant-abc-123 login") == "tenant [REDACTED] login"


def test_configured_values_shorter_than_the_minimum_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A two-character "secret" would replace ordinary words throughout every prompt.
    monkeypatch.setenv("E2E_TEST_SHORT_SECRET", "xy")
    _configure_sensitive(monkeypatch, env_var="E2E_TEST_SHORT_SECRET", literal="abc")
    record = RedactionRecord()

    assert redact_text("abc and xy stay", record=record) == "abc and xy stay"
    assert record.count == 0


def test_the_redaction_marker_is_never_replaced_again(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_sensitive(monkeypatch, literal=REDACTED)

    assert redact_text(f"already {REDACTED}") == f"already {REDACTED}"


def test_a_secret_containing_another_configured_secret_is_replaced_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_sensitive(monkeypatch, literal="abcd,abcd-efgh")

    # Longest first; otherwise the shorter value would be replaced inside the longer one and
    # leave a stale suffix behind.
    assert redact_text("value abcd-efgh") == "value [REDACTED]"


def test_a_url_with_nothing_sensitive_is_neither_changed_nor_counted() -> None:
    # Over-reporting a redaction is as misleading as missing one: an untouched URL is not a
    # replacement the boundary performed.
    record = RedactionRecord()

    assert redact_text("see https://example.test/docs?page=2", record=record) == (
        "see https://example.test/docs?page=2"
    )
    assert record.applied is False


def test_a_redacted_url_query_value_is_counted() -> None:
    record = RedactionRecord()

    assert redact_text("see https://example.test/?token=secret", record=record) == (
        "see https://example.test/?token=%5BREDACTED%5D"
    )
    assert record.count == 1


def test_configured_secrets_reach_the_string_leaves_of_a_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_sensitive(monkeypatch, literal="tenant-abc-123")

    assert redact_value({"dom": ["text tenant-abc-123"]}) == {"dom": ["text [REDACTED]"]}
