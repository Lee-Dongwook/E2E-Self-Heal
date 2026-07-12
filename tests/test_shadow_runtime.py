import pytest

from app.shadow import (
    IShadowRuntime,
    ShadowConfig,
    ShadowRuntime,
    ShadowWorkspace,
)
from app.shadow.injector import MockInjector
from app.shadow.snapshot_store import SnapshotStore


def _make_runtime(tmp_path) -> ShadowRuntime:
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path)))
    store = SnapshotStore(ws)
    injector = MockInjector()
    return ShadowRuntime(workspace=ws, snapshot_store=store, injector=injector)


def test_shadow_runtime_is_importable_and_conforms_to_interface(tmp_path):
    runtime = _make_runtime(tmp_path)
    assert isinstance(runtime, IShadowRuntime)


def test_shadow_runtime_wires_injected_components(tmp_path):
    runtime = _make_runtime(tmp_path)
    assert runtime.workspace is not None
    assert runtime.snapshot_store is not None
    assert runtime.injector is not None


@pytest.mark.parametrize("method_name", ["setup", "teardown"])
def test_shadow_runtime_methods_are_placeholders(tmp_path, method_name):
    runtime = _make_runtime(tmp_path)
    with pytest.raises(NotImplementedError):
        getattr(runtime, method_name)()


def test_shadow_runtime_replay_is_placeholder(tmp_path):
    runtime = _make_runtime(tmp_path)
    with pytest.raises(NotImplementedError):
        runtime.replay("some-snapshot-id")
