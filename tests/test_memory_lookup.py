import pytest
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.healing_history import HealingHistoryRecord, append_record, normalize_error_signature
from app.nodes.memory_lookup import memory_lookup
from app.registry import classify_selector_kind
from app.schemas import PatchInstruction
from app.state import AgentState


def _state() -> AgentState:
    return {
        "test_script_path": "tests/login.spec.ts",
        "original_code": "await page.click('#old')\n",
        "current_code": "await page.click('#old')\n",
        "error_log": "Error: waiting for locator('#old') timed out",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }


def test_memory_lookup_applies_rebased_guarded_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    error = "Error: waiting for locator('#old') timed out"
    record = HealingHistoryRecord(
        normalized_error_signature=normalize_error_signature(error),
        selector_kind=classify_selector_kind("#old"),
        original_selector="#old",
        replacement_selector="#new",
        instruction=PatchInstruction(
            line=99,
            original="await page.click('#old')",
            replacement="await page.click('#new')",
            reason="selector renamed",
            selector="#new",
        ),
        test_script_path="tests/login.spec.ts",
        provider="test",
        model="test",
        source="llm",
        recorded_at=datetime.now(UTC),
    )
    assert append_record(record) is True

    state = _state()
    state["test_script_path"] = str(tmp_path / "tests" / "login.spec.ts")
    result = memory_lookup(state)

    assert result["current_code"] == "await page.click('#new')\n"
    assert result["memory_report"]["active"] is True
    assert result["patch_instructions"]["instructions"][0]["line"] == 1


def test_memory_lookup_rejects_duplicate_source_lines_without_mutating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    error = "Error: waiting for locator('#old') timed out"
    record = HealingHistoryRecord(
        normalized_error_signature=normalize_error_signature(error),
        selector_kind=classify_selector_kind("#old"),
        original_selector="#old",
        replacement_selector="#new",
        instruction=PatchInstruction(
            line=1,
            original="await page.click('#old')",
            replacement="await page.click('#new')",
            reason="selector renamed",
            selector="#new",
        ),
        test_script_path="tests/login.spec.ts",
        provider="test",
        model="test",
        source="llm",
        recorded_at=datetime.now(UTC),
    )
    assert append_record(record) is True
    state = _state()
    state["test_script_path"] = str(tmp_path / "tests" / "login.spec.ts")
    state["current_code"] = "await page.click('#old')\nawait page.click('#old')\n"

    result = memory_lookup(state)

    assert result["memory_report"]["hit"] is False
    assert "matched 2" in result["memory_report"]["rejection"]
