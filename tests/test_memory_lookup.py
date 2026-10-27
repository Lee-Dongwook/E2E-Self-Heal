import pytest
from datetime import UTC, datetime
from pathlib import Path

import app.cli as cli_module
from app.config import settings
from app.healing_history import (
    HealingHistoryRecord,
    append_record,
    find_match,
    make_record,
    normalize_error_signature,
)
from app.nodes.memory_lookup import memory_lookup
from app.preprocess.error_log_parser import parse_error_log
from app.registry import classify_selector_kind
from app.schemas import PatchInstruction, RefusalReason, RefusalReport
from app.state import AgentState
from tests.factories import make_state


def _state() -> AgentState:
    return make_state(
        test_script_path="tests/login.spec.ts",
        original_code="await page.click('#old')\n",
        current_code="await page.click('#old')\n",
        error_log="Error: waiting for locator('#old') timed out",
    )


def test_memory_lookup_is_bypassed_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    state["memory_enabled"] = False
    monkeypatch.setattr(
        "app.nodes.memory_lookup.find_match",
        lambda *_: pytest.fail("disabled memory must not query healing history"),
    )

    result = memory_lookup(state)

    assert result["memory_report"] == {"attempted": False, "enabled": False}
    assert result["evidence_history"] == [
        {
            "loop_count": 0,
            "stage": "memory_lookup",
            "outcome": "disabled",
            "details": {},
        }
    ]


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
        replacement_selectors=("#new",),
        instructions=[
            PatchInstruction(
                line=99,
                original="await page.click('#old')",
                replacement="await page.click('#new')",
                reason="selector renamed",
                selector="#new",
            )
        ],
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


def test_memory_lookup_rejects_a_history_candidate_that_violates_the_ast_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    error = "Error: waiting for locator('#old') timed out"
    original = "const submit = page.locator('#old');"
    record = HealingHistoryRecord(
        normalized_error_signature=normalize_error_signature(error),
        selector_kind=classify_selector_kind("#old"),
        original_selector="#old",
        replacement_selectors=("#new",),
        instructions=[
            PatchInstruction(
                line=1,
                original=original,
                # Passes every per-line gate but appends a statement, so only the AST lock
                # can reject it. A cached repair gets no shortcut around that check.
                replacement="const submit = page.locator('#new'); await page.goto('/admin');",
                reason="selector renamed",
                selector="#new",
            )
        ],
        test_script_path="tests/login.spec.ts",
        provider="test",
        model="test",
        source="llm",
        recorded_at=datetime.now(UTC),
    )
    assert append_record(record) is True
    state = _state()
    state["test_script_path"] = str(tmp_path / "tests" / "login.spec.ts")
    state["current_code"] = f"{original}\n"

    result = memory_lookup(state)

    assert "current_code" not in result
    # A rejected history candidate is a cache miss: it must not spend a repair loop.
    assert "loop_count" not in result
    assert result["memory_report"]["hit"] is False
    assert "AST lock rejected" in result["memory_report"]["rejection"]
    candidate = result["evidence_candidates"][0]
    assert candidate["source"] == "memory"
    assert candidate["outcome"] == "rejected"
    assert candidate["ast_lock"]["reason"] == "disallowed_ast_change"
    assert candidate["ast_lock"]["node_kind"]
    assert candidate["ast_lock"]["line"] == 1


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
        replacement_selectors=("#new",),
        instructions=[
            PatchInstruction(
                line=1,
                original="await page.click('#old')",
                replacement="await page.click('#new')",
                reason="selector renamed",
                selector="#new",
            )
        ],
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
    candidate = result["evidence_candidates"][0]
    assert candidate["source"] == "memory"
    assert candidate["memory_score"] is not None
    assert candidate["outcome"] == "rejected"
    assert "matched 2" in candidate["rejection"]
    # Not an AST lock rejection, so no lock verdict is attributed.
    assert "ast_lock" not in candidate


def test_a_remembered_repair_cannot_pre_empt_the_ambiguous_target_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Learning may save a model call, never lower the refusal bar (Issue #308)."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    original = "await page.click('#old')\n"
    spec = tmp_path / "t.spec.ts"
    spec.write_text(original)
    raw_log = "Error: waiting for locator('#old') timed out"
    record = make_record(
        error_log=parse_error_log(raw_log),
        instructions=[
            PatchInstruction(
                line=1,
                original=original.strip(),
                replacement="await page.click('#save')",
                reason="an earlier verified repair",
                selector="#save",
            )
        ],
        test_script_path=str(spec),
        source="llm",
    )
    assert record is not None
    assert append_record(record) is True
    # Without the refusal check this history would apply: the record matches this failure.
    assert find_match(parse_error_log(raw_log), str(spec))[0] is not None
    monkeypatch.setattr(
        "app.nodes.memory_lookup.find_match",
        lambda *_: pytest.fail("an ambiguous target must be refused before history is consulted"),
    )
    old = {"tag": "button", "attributes": {"id": "old", "type": "submit"}}
    look_alikes = [
        {"tag": "button", "attributes": {"id": id_, "type": "submit"}}
        for id_ in ("save", "publish")
    ]
    dom_diff_context = [
        {"file": "src/App.tsx", "line": 0, "previous": old, "current": {}},
        {"file": "src/App.tsx", "line": 4, "previous": {}, "current": look_alikes[0]},
        {"file": "src/App.tsx", "line": 7, "previous": {}, "current": look_alikes[1]},
    ]

    result = cli_module._heal_file(spec, raw_log, dom_diff_context, dry_run=False)

    assert isinstance(result, RefusalReport)
    assert result.reason is RefusalReason.AMBIGUOUS_TARGET
    assert result.evidence.candidates == []
    assert spec.read_text() == original
