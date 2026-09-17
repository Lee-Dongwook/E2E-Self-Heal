import json
import stat

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
from app.shadow.redaction import redact_url, redact_value
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
