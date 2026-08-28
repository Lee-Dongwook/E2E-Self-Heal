"""Selector Verifier node + its routing, with the Node/Playwright helper mocked out."""

from langgraph.graph import END

import app.nodes.selector_verifier as verifier_node
from app.config import settings
from app.graph import route_after_verify
from app.nodes.selector_verifier import _revert, selector_verifier
from app.schemas import PatchInstruction, PatchOutput
from app.state import AgentState

ORIGINAL = "await page.click('#old')\n"
PATCHED = "await page.click('#new')\n"


def _instruction(selector: str) -> PatchInstruction:
    return PatchInstruction(
        line=1,
        original=ORIGINAL.strip(),
        replacement=PATCHED.strip(),
        reason="renamed",
        selector=selector,
    )


def _state(selector: str = "#new", **overrides) -> AgentState:
    base: AgentState = {
        "test_script_path": "t.spec.ts",
        "original_code": ORIGINAL,
        "current_code": PATCHED,
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "diagnosis",
        "patch_instructions": PatchOutput(instructions=[_instruction(selector)]).model_dump(),
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def test_revert_restores_original_line():
    assert _revert(PATCHED, [_instruction("#new")]) == ORIGINAL


def test_revert_preserves_crlf_line_ending():
    # Reverting must not silently normalize CRLF to LF (regression: "\r\n".endswith("\n")).
    patched_crlf = "await page.click('#new')\r\n"
    assert _revert(patched_crlf, [_instruction("#new")]) == "await page.click('#old')\r\n"


def test_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "verify_selectors", False)
    monkeypatch.setattr(settings, "app_url", "http://localhost:4173")
    result = selector_verifier(_state())
    assert result["verification_report"] == {"skipped": True, "ok": True}


def test_skips_when_no_app_url(monkeypatch):
    monkeypatch.setattr(settings, "verify_selectors", True)
    monkeypatch.setattr(settings, "app_url", "")
    result = selector_verifier(_state())
    assert result["verification_report"]["ok"] is True


def test_skips_when_helper_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "verify_selectors", True)
    monkeypatch.setattr(settings, "app_url", "http://localhost:4173")
    monkeypatch.setattr(verifier_node, "check_selectors", lambda url, sels: None)
    result = selector_verifier(_state())
    assert result["verification_report"] == {"skipped": True, "ok": True}


def test_passes_on_unique_match(monkeypatch):
    monkeypatch.setattr(settings, "verify_selectors", True)
    monkeypatch.setattr(settings, "app_url", "http://localhost:4173")
    monkeypatch.setattr(verifier_node, "check_selectors", lambda url, sels: {"#new": 1})
    result = selector_verifier(_state("#new"))
    assert result["verification_report"] == {"ok": True, "counts": {"#new": 1}}
    assert result["rollback_code"] == PATCHED
    assert "current_code" not in result  # nothing reverted


def test_rejects_and_reverts_on_zero_match(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "verify_selectors", True)
    monkeypatch.setattr(settings, "app_url", "http://localhost:4173")
    monkeypatch.setattr(verifier_node, "check_selectors", lambda url, sels: {"#new": 0})
    test_file = tmp_path / "t.spec.ts"
    test_file.write_text(PATCHED)
    result = selector_verifier(
        _state("#new", test_script_path=str(test_file), rollback_code=ORIGINAL)
    )
    assert result["verification_report"]["ok"] is False
    assert result["current_code"] == ORIGINAL  # patch reverted
    assert result["rollback_code"] == ORIGINAL
    assert test_file.read_text() == ORIGINAL
    assert result["loop_count"] == 1
    assert "VERIFICATION FEEDBACK" in result["analysis_report"]


def test_route_runs_test_when_verified():
    assert route_after_verify(_state(verification_report={"ok": True})) == "test_runner"


def test_route_repatches_when_rejected_under_cap():
    state = _state(verification_report={"ok": False}, loop_count=1)
    assert route_after_verify(state) == "patch_generator"


def test_route_ends_when_rejected_at_cap():
    state = _state(verification_report={"ok": False}, loop_count=settings.max_loops)
    assert route_after_verify(state) == END
