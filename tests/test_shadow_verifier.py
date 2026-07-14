

from langgraph.graph import END
from app.graph import route_after_shadow
from app.nodes.shadow_verifier import shadow_verifier
from app.shadow.config import ShadowConfig
from app.shadow.schemas import (
    CapturedRequest,
    CapturedResponse,
    NetworkSnapshot,
    ShadowRunResult,
    ShadowSnapshot,
)
from app.shadow.snapshot_store import SnapshotStore
from app.shadow.workspace import ShadowWorkspace
from app.state import AgentState


def test_shadow_verifier_skips_when_no_snapshot(tmp_path, monkeypatch):
    test_file = tmp_path / "login.spec.ts"
    test_file.write_text("console.log('original');", encoding="utf-8")

    ws_dir = tmp_path / "shadow"
    monkeypatch.setattr(
        "app.nodes.shadow_verifier.ShadowConfig",
        lambda: ShadowConfig(workspace_dir=str(ws_dir)),
    )

    state: AgentState = {
        "test_script_path": str(test_file),
        "original_code": "console.log('original');",
        "current_code": "console.log('patched');",
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "Original analysis report",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }

    result = shadow_verifier(state)

    assert result == {"shadow_report": {"ok": True, "skipped": True}}
    # Check that code on disk was not modified
    assert test_file.read_text(encoding="utf-8") == "console.log('original');"


def test_shadow_verifier_passes_on_successful_replay(tmp_path, monkeypatch):
    test_file = tmp_path / "login.spec.ts"
    test_file.write_text("console.log('original');", encoding="utf-8")

    ws_dir = tmp_path / "shadow"
    config = ShadowConfig(workspace_dir=str(ws_dir))
    ws = ShadowWorkspace(config)
    store = SnapshotStore(ws)

    # Save a fake snapshot so the file check passes
    snapshot = ShadowSnapshot(
        snapshot_id="login.spec",
        network_snapshots=[
            NetworkSnapshot(
                request=CapturedRequest(method="GET", url="https://api.example.com"),
                response=CapturedResponse(status=200, body="ok"),
            )
        ],
    )
    store.save_snapshot("login.spec", snapshot)

    monkeypatch.setattr(
        "app.nodes.shadow_verifier.ShadowConfig",
        lambda: config,
    )

    # Mock run_shadow to return a passing result
    mock_run_result = ShadowRunResult(
        is_success=True,
        matched_count=1,
        missed_count=0,
        score=100.0,
    )
    monkeypatch.setattr(
        "app.nodes.shadow_verifier.run_shadow",
        lambda *args, **kwargs: mock_run_result,
    )

    state: AgentState = {
        "test_script_path": str(test_file),
        "original_code": "console.log('original');",
        "current_code": "console.log('patched');",
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "Original analysis report",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }

    result = shadow_verifier(state)

    assert result == {
        "current_code": "console.log('patched');",
        "shadow_report": {"ok": True, "score": 100.0},
    }
    # The patched code is kept on disk for further stages
    assert test_file.read_text(encoding="utf-8") == "console.log('patched');"


def test_shadow_verifier_fails_and_reverts_on_failed_replay(tmp_path, monkeypatch):
    test_file = tmp_path / "login.spec.ts"
    test_file.write_text("console.log('original');\nconsole.log('second-line');", encoding="utf-8")

    ws_dir = tmp_path / "shadow"
    config = ShadowConfig(workspace_dir=str(ws_dir))
    ws = ShadowWorkspace(config)
    store = SnapshotStore(ws)

    snapshot = ShadowSnapshot(
        snapshot_id="login.spec",
        network_snapshots=[],
    )
    store.save_snapshot("login.spec", snapshot)

    monkeypatch.setattr(
        "app.nodes.shadow_verifier.ShadowConfig",
        lambda: config,
    )

    # Mock run_shadow to return a failing result
    mock_run_result = ShadowRunResult(
        is_success=False,
        matched_count=0,
        missed_count=2,
        score=0.0,
    )
    monkeypatch.setattr(
        "app.nodes.shadow_verifier.run_shadow",
        lambda *args, **kwargs: mock_run_result,
    )

    state: AgentState = {
        "test_script_path": str(test_file),
        "original_code": "console.log('original');\nconsole.log('second-line');",
        "current_code": "console.log('patched');\nconsole.log('second-line');",
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "Original analysis report",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 1,
        "is_success": False,
    }

    result = shadow_verifier(state)

    assert result["shadow_report"] == {"ok": False, "score": 0.0}
    assert result["loop_count"] == 2
    assert "[SHADOW VERIFICATION FEEDBACK]" in result["analysis_report"]
    # Reverted in current_code state to original_code
    assert result["current_code"] == "console.log('original');\nconsole.log('second-line');"
    # Reverted on disk
    assert (
        test_file.read_text(encoding="utf-8")
        == "console.log('original');\nconsole.log('second-line');"
    )


def test_shadow_verifier_reverts_on_placeholder_result(tmp_path, monkeypatch):
    test_file = tmp_path / "login.spec.ts"
    test_file.write_text("console.log('original');", encoding="utf-8")

    ws_dir = tmp_path / "shadow"
    config = ShadowConfig(workspace_dir=str(ws_dir))
    ws = ShadowWorkspace(config)
    store = SnapshotStore(ws)

    # Save a fake snapshot so the file check passes
    snapshot = ShadowSnapshot(
        snapshot_id="login.spec",
        network_snapshots=[],
    )
    store.save_snapshot("login.spec", snapshot)

    monkeypatch.setattr(
        "app.nodes.shadow_verifier.ShadowConfig",
        lambda: config,
    )

    # Mock run_shadow to return a placeholder string
    monkeypatch.setattr(
        "app.nodes.shadow_verifier.run_shadow",
        lambda *args, **kwargs: "Shadow Testing runtime is under development",
    )

    state: AgentState = {
        "test_script_path": str(test_file),
        "original_code": "console.log('original');",
        "current_code": "console.log('patched');",
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "Original analysis report",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }

    result = shadow_verifier(state)

    assert result == {"shadow_report": {"ok": True, "skipped": True}}
    # Check that disk is reverted to original_code
    assert test_file.read_text(encoding="utf-8") == "console.log('original');"


def test_route_after_shadow():
    # 1. Ok / Skipped -> selector_verifier
    state_ok: AgentState = {
        "test_script_path": "",
        "original_code": "",
        "current_code": "",
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "",
        "patch_instructions": {},
        "verification_report": {},
        "shadow_report": {"ok": True, "skipped": True},
        "loop_count": 0,
        "is_success": False,
    }
    assert route_after_shadow(state_ok) == "selector_verifier"

    # 2. Failed, loop_count under cap -> patch_generator
    state_fail: AgentState = {
        "test_script_path": "",
        "original_code": "",
        "current_code": "",
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "",
        "patch_instructions": {},
        "verification_report": {},
        "shadow_report": {"ok": False},
        "loop_count": 1,
        "is_success": False,
    }
    assert route_after_shadow(state_fail) == "patch_generator"

    # 3. Failed, loop_count at cap -> END
    state_cap: AgentState = {
        "test_script_path": "",
        "original_code": "",
        "current_code": "",
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "",
        "patch_instructions": {},
        "verification_report": {},
        "shadow_report": {"ok": False},
        "loop_count": 3,
        "is_success": False,
    }
    assert route_after_shadow(state_cap) == END
