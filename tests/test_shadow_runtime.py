from app.shadow import (
    IShadowRuntime,
    ShadowConfig,
    ShadowRuntime,
    ShadowWorkspace,
)
from app.shadow.injector import MockInjector
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
    ws = ShadowWorkspace(tmp_path)
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
