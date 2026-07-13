from app.shadow import CleanupPolicy, ShadowConfig, ShadowWorkspace

import pytest


def test_workspace_derives_all_paths_from_config(tmp_path):
    config = ShadowConfig(
        workspace_dir=str(tmp_path / "ws"),
        cache_dir="c",
        snapshots_dir="s",
        tmp_dir="t",
    )
    ws = ShadowWorkspace(config)

    assert ws.base_dir == (tmp_path / "ws").resolve()
    assert ws.cache_dir == ws.base_dir / "c"
    assert ws.snapshots_dir == ws.base_dir / "s"
    assert ws.tmp_dir == ws.base_dir / "t"


def test_workspace_creates_directory_tree(tmp_path):
    ws = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path / "ws")))

    assert ws.base_dir.is_dir()
    assert ws.cache_dir.is_dir()
    assert ws.snapshots_dir.is_dir()
    assert ws.tmp_dir.is_dir()


def test_cleanup_never_keeps_workspace(tmp_path):
    ws = ShadowWorkspace(
        ShadowConfig(workspace_dir=str(tmp_path / "ws"), cleanup_policy=CleanupPolicy.NEVER)
    )

    ws.cleanup(is_success=True)

    assert ws.base_dir.exists()


def test_cleanup_on_success_keeps_workspace_on_failure(tmp_path):
    ws = ShadowWorkspace(
        ShadowConfig(workspace_dir=str(tmp_path / "ws"), cleanup_policy=CleanupPolicy.ON_SUCCESS)
    )

    ws.cleanup(is_success=False)

    assert ws.base_dir.exists()


def test_cleanup_on_success_removes_workspace_on_success(tmp_path):
    ws = ShadowWorkspace(
        ShadowConfig(workspace_dir=str(tmp_path / "ws"), cleanup_policy=CleanupPolicy.ON_SUCCESS)
    )

    ws.cleanup(is_success=True)

    assert not ws.base_dir.exists()


def test_cleanup_always_removes_workspace_regardless_of_outcome(tmp_path):
    ws = ShadowWorkspace(
        ShadowConfig(workspace_dir=str(tmp_path / "ws"), cleanup_policy=CleanupPolicy.ALWAYS)
    )

    ws.cleanup(is_success=False)

    assert not ws.base_dir.exists()

def test_shadow_workspace_helper_paths_use_expected_directories(tmp_path):
    workspace = ShadowWorkspace(
        ShadowConfig(
            workspace_dir=str(tmp_path / "shadow"),
            cache_dir="c",
            snapshots_dir="s",
            tmp_dir="t",
        )
    )

    assert workspace.cache_path("trace.zip") == workspace.base_dir / "c" / "trace.zip"
    assert workspace.snapshot_path("state.json") == workspace.base_dir / "s" / "state.json"
    assert workspace.tmp_path("run/output.txt") == workspace.base_dir / "t" / "run" / "output.txt"


def test_shadow_workspace_helper_paths_reject_parent_traversal(tmp_path):
    workspace = ShadowWorkspace(ShadowConfig(workspace_dir=str(tmp_path / "shadow")))

    with pytest.raises(ValueError):
        workspace.cache_path("../outside-cache")
