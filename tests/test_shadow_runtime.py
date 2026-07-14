import subprocess
from unittest.mock import MagicMock

from app.shadow import (
    CapturedRequest,
    CapturedResponse,
    IShadowRuntime,
    MockInjector,
    NetworkSnapshot,
    ShadowConfig,
    ShadowRunResult,
    ShadowRuntime,
    ShadowSnapshot,
    ShadowWorkspace,
)
from app.shadow.context import ShadowContext
from app.shadow.runtime import SHADOW_PLACEHOLDER_MESSAGE, run_shadow
from app.shadow.snapshot_store import SnapshotStore


def _make_runtime(tmp_path) -> ShadowRuntime:
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
    store = SnapshotStore(ws)
    injector = MockInjector()
    return ShadowRuntime(workspace=ws, snapshot_store=store, injector=injector)


def test_shadow_runtime_is_importable_and_conforms_to_interface(tmp_path):
    runtime = _make_runtime(tmp_path)
    assert isinstance(runtime, IShadowRuntime)


def test_minimal_runtime_can_be_created_without_collaborators():
    runtime = ShadowRuntime()
    assert runtime.workspace is None
    assert runtime.snapshot_store is None
    assert runtime.injector is None
    assert runtime.context is None
    assert runtime.is_active is False


def test_initialize_creates_and_activates_context():
    runtime = ShadowRuntime()
    runtime.initialize()
    assert runtime.is_active is True
    assert isinstance(runtime.context, ShadowContext)
    assert runtime.context.is_active is True


def test_shutdown_deactivates_and_releases_context():
    runtime = ShadowRuntime()
    runtime.initialize()
    runtime.shutdown()
    assert runtime.is_active is False
    assert runtime.context is None


def test_initialize_is_idempotent():
    runtime = ShadowRuntime()
    runtime.initialize()
    first = runtime.context
    runtime.initialize()
    assert runtime.context is first


def test_shutdown_is_idempotent_without_initialize():
    runtime = ShadowRuntime()
    runtime.shutdown()
    assert runtime.context is None
    assert runtime.is_active is False


def test_context_carries_injected_collaborators(tmp_path):
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
    store = SnapshotStore(ws)
    injector = MockInjector()
    runtime = ShadowRuntime(workspace=ws, snapshot_store=store, injector=injector)
    runtime.initialize()
    assert runtime.context is not None
    assert runtime.context.workspace is ws
    assert runtime.context.snapshot_store is store
    assert runtime.context.injector is injector


def test_run_shadow_exercises_lifecycle_and_returns_message():
    assert run_shadow() == SHADOW_PLACEHOLDER_MESSAGE


def test_run_shadow_with_mock_playwright_and_snapshots(tmp_path, monkeypatch):
    ws_dir = tmp_path / "shadow"
    config = ShadowConfig(workspace_dir=str(ws_dir))
    ws = ShadowWorkspace(config)
    store = SnapshotStore(ws)

    snapshot = ShadowSnapshot(
        snapshot_id="test_snap",
        network_snapshots=[
            NetworkSnapshot(
                request=CapturedRequest(method="GET", url="https://api.example.com/data"),
                response=CapturedResponse(status=200, body="mocked_body"),
            )
        ],
    )
    store.save_snapshot("test_snap", snapshot)

    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("console.log('test');")

    # Mock subprocess.run
    subprocess_called = []

    def mock_subprocess_run(cmd, **kwargs):
        subprocess_called.append(cmd)
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    # Turn off sandbox so the tmp config write is allowed during the test
    monkeypatch.setattr(
        "app.shadow.runtime.assert_write_allowed", lambda path, reason="write": None
    )
    monkeypatch.setattr(
        "app.shadow.runtime.assert_command_allowed", lambda cmd, reason="subprocess": None
    )

    # Mock _get_free_port and _fetch_ws_endpoint
    monkeypatch.setattr("app.shadow.runtime._get_free_port", lambda: 19999)
    monkeypatch.setattr(
        "app.shadow.runtime._fetch_ws_endpoint",
        lambda port, timeout=10.0: "ws://localhost:19999/devtools/browser/test-id",
    )

    # Build mock playwright context, browser
    mock_context = MagicMock()
    matched_route = MagicMock()
    matched_request = MagicMock()
    matched_request.method = "GET"
    matched_request.url = "https://api.example.com/data"
    matched_request.headers = {}
    matched_request.post_data = None

    # When route() is called on the context, simulate the handler being triggered
    def fake_route(pattern, handler):
        handler(matched_route, matched_request)

    mock_context.route = fake_route

    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_context

    mock_playwright = MagicMock()
    mock_playwright.chromium.launch.return_value = mock_browser

    class MockSyncPlaywright:
        def __enter__(self):
            return mock_playwright

        def __exit__(self, *args):
            pass

    monkeypatch.setattr("app.shadow.runtime.sync_playwright", MockSyncPlaywright)

    result = run_shadow(test_path=test_file, snapshot_id="test_snap", config=config)

    assert isinstance(result, ShadowRunResult)
    assert result.is_success is True
    assert result.matched_count == 1
    assert result.missed_count == 0
    assert result.score > 0
    assert len(subprocess_called) == 1
    assert "--config" in subprocess_called[0]
