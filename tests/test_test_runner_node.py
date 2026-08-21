from pathlib import Path

import pytest

import app.nodes.test_runner as test_runner_node
from app.state import AgentState


def test_memory_test_failure_restores_original_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_file = tmp_path / "login.spec.ts"
    original = "await page.click('#old')\n"
    candidate = "await page.click('#new')\n"
    test_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(test_runner_node, "run_playwright", lambda _: (False, "still failing"))
    state: AgentState = {
        "test_script_path": str(test_file),
        "original_code": original,
        "current_code": candidate,
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "",
        "patch_instructions": {},
        "verification_report": {},
        "memory_report": {"active": True},
        "loop_count": 0,
        "is_success": False,
    }

    result = test_runner_node.test_runner(state)

    assert result["is_success"] is False
    assert result["current_code"] == original
    assert result["loop_count"] == 0
    assert test_file.read_text(encoding="utf-8") == original
