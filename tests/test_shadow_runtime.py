import subprocess
from unittest.mock import MagicMock
import pytest

from app.config import settings
from app.shadow import (
    CapturedRequest,
    CapturedResponse,
    IShadowRuntime,
    LocalStorageSnapshot,
    MockInjector,
    NetworkSnapshot,
    ShadowConfig,
    ShadowRunResult,
    ShadowRuntime,
    ShadowSnapshot,
    ShadowWorkspace,
)
from app.shadow.context import ShadowContext
from app.shadow.runtime import SHADOW_PLACEHOLDER_MESSAGE, _build_run_result, run_shadow
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


@pytest.mark.parametrize(
    ("matched_scores", "missed_count", "expected_score"),
    [
        ([80.0, 100.0], 0, 90.0),
        ([80.0, 100.0], 3, 90.0),
        ([], 2, 0.0),
    ],
    ids=["matched-only", "mixed", "zero-match"],
)
def test_build_run_result_averages_only_matched_requests(
    matched_scores: list[float], missed_count: int, expected_score: float
) -> None:
    injector = MockInjector()
    injector.matched_requests = [
        (
            CapturedRequest(method="GET", url=f"https://api.example.com/matched/{index}"),
            score,
        )
        for index, score in enumerate(matched_scores)
    ]
    injector.unmatched_requests = [
        CapturedRequest(method="GET", url=f"https://api.example.com/missed/{index}")
        for index in range(missed_count)
    ]

    result = _build_run_result(is_success=True, injector=injector)

    assert result.matched_count == len(matched_scores)
    assert result.missed_count == missed_count
    assert result.missed_requests == injector.unmatched_requests
    assert result.score == pytest.approx(expected_score)


@pytest.mark.parametrize(
    ("state_snapshots", "expected_context_kwargs"),
    [
        ([], {}),
        (
            [
                LocalStorageSnapshot(
                    origin="https://app.example.com",
                    items={"theme": "dark"},
                )
            ],
            {
                "storage_state": {
                    "cookies": [],
                    "origins": [
                        {
                            "origin": "https://app.example.com",
                            "localStorage": [{"name": "theme", "value": "dark"}],
                        }
                    ],
                }
            },
        ),
    ],
    ids=["legacy-http-only", "local-storage"],
)
def test_run_shadow_with_mock_playwright_and_snapshots(
    tmp_path, monkeypatch, state_snapshots, expected_context_kwargs
):
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
        state_snapshots=state_snapshots,
    )
    store.save_snapshot("test_snap", snapshot)

    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("console.log('test');")

    # Mock subprocess.Popen
    subprocess_called = []

    def mock_subprocess_popen(cmd, **kwargs):
        subprocess_called.append(cmd)
        process = MagicMock(returncode=0)
        process.communicate.return_value = ("", "")
        return process

    monkeypatch.setattr(subprocess, "Popen", mock_subprocess_popen)

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
    mock_browser.new_context.assert_called_once_with(**expected_context_kwargs)
    assert store.get_snapshot("test_snap").snapshot_id == "test_snap"
    assert ws.snapshots_dir.is_dir()
    assert not ws.cache_dir.exists()
    assert not ws.tmp_dir.exists()


def test_run_shadow_timeout_terminates_the_full_process_tree(tmp_path, monkeypatch):
    ws_dir = tmp_path / "shadow"
    config = ShadowConfig(workspace_dir=str(ws_dir))
    ws = ShadowWorkspace(config)
    SnapshotStore(ws).save_snapshot("test_snap", ShadowSnapshot(snapshot_id="test_snap"))
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("console.log('test');")

    process = MagicMock()
    process.communicate.side_effect = [
        subprocess.TimeoutExpired("npx", 7, output="partial out", stderr="partial err"),
        ("", ""),
    ]
    process.poll.return_value = None
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("app.shadow.runtime._terminate_process_tree", lambda child: child.kill())
    monkeypatch.setattr(settings, "test_timeout_seconds", 7)
    monkeypatch.setattr("app.shadow.runtime.assert_write_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.shadow.runtime.assert_command_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.shadow.runtime._get_free_port", lambda: 19999)
    monkeypatch.setattr("app.shadow.runtime._fetch_ws_endpoint", lambda *args: "ws://example")

    context = MagicMock()
    browser = MagicMock()
    browser.new_context.return_value = context
    playwright = MagicMock()
    playwright.chromium.launch.return_value = browser

    class MockSyncPlaywright:
        def __enter__(self):
            return playwright

        def __exit__(self, *args):
            return None

    monkeypatch.setattr("app.shadow.runtime.sync_playwright", MockSyncPlaywright)

    result = run_shadow(test_path=test_file, snapshot_id="test_snap", config=config)

    assert isinstance(result, ShadowRunResult)
    assert result.is_success is False
    process.kill.assert_called_once()
    assert process.communicate.call_args_list[0].kwargs["timeout"] == 7
    assert process.communicate.call_args_list[1].kwargs["timeout"] == 5
