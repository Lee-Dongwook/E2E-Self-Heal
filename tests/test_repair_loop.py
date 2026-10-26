"""End-to-end graph test with the LLM and Playwright mocked out."""

import json
import pathlib

import pytest

import app.cli as cli_module
import app.nodes.decline_adjudicator as adjudicator_node
import app.nodes.diagnoser as diagnoser_node
import app.nodes.patch_generator as patch_node
import app.nodes.test_runner as runner_node
from app.config import settings
from app.graph import build_graph
from app.healing_history import append_record, make_record
from app.preprocess.error_log_parser import parse_error_log
from app.schemas import (
    SCHEMA_VERSION,
    DiagnosisVerdict,
    DiagnosisVerdictOutput,
    PatchInstruction,
    PatchOutput,
    RefusalReason,
)
from app.state import AgentState
from tests.factories import make_state, make_verdict

ORIGINAL = "await page.click('#old')\n"
FIXED = "await page.click('#new')\n"


def _initial_state() -> AgentState:
    return make_state(
        test_script_path="",  # set per test to the tmp file
        original_code=ORIGINAL,
        current_code=ORIGINAL,
        error_log="Error: Timeout\nwaiting for locator('#old')",
    )


def _declined_output() -> PatchOutput:
    return PatchOutput(instructions=[])


def _patch_output() -> PatchOutput:
    return PatchOutput(
        instructions=[
            PatchInstruction(
                line=1,
                original=ORIGINAL.strip(),
                replacement=FIXED.strip(),
                reason="selector renamed",
            )
        ]
    )


def test_loop_heals_on_first_rerun(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    spec = tmp_path / "t.spec.ts"
    spec.write_text(ORIGINAL)

    monkeypatch.setattr(diagnoser_node, "generate_diagnosis", lambda s, u: "selector changed")
    monkeypatch.setattr(patch_node, "generate_patch", lambda s, u: _patch_output())
    # Pass once the file on disk contains the fixed selector.
    monkeypatch.setattr(
        runner_node, "run_playwright", lambda path: ("#new" in open(path).read(), "Error: Timeout")
    )

    state = _initial_state()
    state["test_script_path"] = str(spec)
    final = build_graph().invoke(state)

    assert final["is_success"] is True
    assert final["loop_count"] == 0
    assert "#new" in spec.read_text()


def test_loop_gives_up_at_cap(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    spec = tmp_path / "t.spec.ts"
    spec.write_text(ORIGINAL)

    monkeypatch.setattr(diagnoser_node, "generate_diagnosis", lambda s, u: "selector changed")
    monkeypatch.setattr(patch_node, "generate_patch", lambda s, u: _patch_output())
    monkeypatch.setattr(runner_node, "run_playwright", lambda path: (False, "Error: still failing"))

    state = _initial_state()
    state["test_script_path"] = str(spec)
    final = build_graph().invoke(state)

    assert final["is_success"] is False
    assert final["loop_count"] == settings.max_loops
    assert final["refusal_reason"] is RefusalReason.INSUFFICIENT_EVIDENCE


def test_loop_reports_provider_error_after_patch_retries_are_exhausted(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    spec = tmp_path / "t.spec.ts"
    spec.write_text(ORIGINAL)

    monkeypatch.setattr(diagnoser_node, "generate_diagnosis", lambda s, u: "selector changed")
    monkeypatch.setattr(
        patch_node,
        "generate_patch",
        lambda s, u: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )
    monkeypatch.setattr(runner_node, "run_playwright", lambda path: (False, "Error: still failing"))

    state = _initial_state()
    state["test_script_path"] = str(spec)
    final = build_graph().invoke(state)

    assert final["refusal_reason"] is RefusalReason.PROVIDER_ERROR


def test_loop_reports_guardrail_rejection_after_retries_are_exhausted(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    spec = tmp_path / "t.spec.ts"
    spec.write_text(ORIGINAL)
    unsafe_patch = PatchOutput(
        instructions=[
            PatchInstruction(
                line=1,
                original=ORIGINAL.strip(),
                replacement="expect(page.locator('#new')).toBeVisible()",
                reason="unsafe assertion change",
            )
        ]
    )

    monkeypatch.setattr(diagnoser_node, "generate_diagnosis", lambda s, u: "selector changed")
    monkeypatch.setattr(patch_node, "generate_patch", lambda s, u: unsafe_patch)

    state = _initial_state()
    state["test_script_path"] = str(spec)
    final = build_graph().invoke(state)

    assert final["refusal_reason"] is RefusalReason.GUARDRAIL_VIOLATION


def test_ast_lock_rejections_still_honor_the_loop_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """A patch only the AST lock can reject must retry, spend loops, and stop at the cap."""
    monkeypatch.chdir(tmp_path)
    original = "const submit = page.locator('#old');\n"
    spec = tmp_path / "t.spec.ts"
    spec.write_text(original)
    # Passes every per-line regex gate, but appends a statement: only the lock rejects it.
    lock_violation = PatchOutput(
        instructions=[
            PatchInstruction(
                line=1,
                original=original.strip(),
                replacement="const submit = page.locator('#new'); await page.goto('/admin');",
                reason="adds navigation",
            )
        ]
    )
    attempts: list[int] = []

    def generate(system: str, user: str) -> PatchOutput:
        attempts.append(1)
        return lock_violation

    monkeypatch.setattr(diagnoser_node, "generate_diagnosis", lambda s, u: "selector changed")
    monkeypatch.setattr(patch_node, "generate_patch", generate)
    monkeypatch.setattr(
        runner_node,
        "run_playwright",
        lambda path: (_ for _ in ()).throw(AssertionError("a rejected patch must not be run")),
    )

    state = _initial_state()
    state["test_script_path"] = str(spec)
    state["original_code"] = state["current_code"] = original
    final = build_graph().invoke(state)

    assert final["is_success"] is False
    assert final["refusal_reason"] is RefusalReason.GUARDRAIL_VIOLATION
    # Every rejection spends a loop, so the generator is called exactly max_loops times.
    assert final["loop_count"] == settings.max_loops
    assert len(attempts) == settings.max_loops
    assert spec.read_text() == original
    candidates = final["evidence_candidates"]
    assert len(candidates) == settings.max_loops
    assert all(c["outcome"] == "rejected" for c in candidates)
    assert all("AST lock rejected" in c["rejection"] for c in candidates)


def test_guardrail_refusal_json_reports_what_the_ast_lock_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """The emitted `--json` payload names the offending node kind and line per refused candidate."""
    monkeypatch.chdir(tmp_path)
    original = "const submit = page.locator('#old');\n"
    spec = tmp_path / "t.spec.ts"
    spec.write_text(original)
    lock_violation = PatchOutput(
        instructions=[
            PatchInstruction(
                line=1,
                original=original.strip(),
                replacement="const submit = page.locator('#new'); await page.goto('/admin');",
                reason="adds navigation",
            )
        ]
    )
    monkeypatch.setattr(diagnoser_node, "generate_diagnosis", lambda s, u: "selector changed")
    monkeypatch.setattr(patch_node, "generate_patch", lambda s, u: lock_violation)

    result = cli_module._heal_file(
        spec, "Error: waiting for locator('#old') timed out", [], dry_run=False
    )
    payload = json.loads(result.model_dump_json())

    assert payload["kind"] == "refusal"
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["reason"] == "guardrail_violation"
    candidates = payload["evidence"]["candidates"]
    assert len(candidates) == settings.max_loops
    for candidate in candidates:
        assert candidate["outcome"] == "rejected"
        assert candidate["ast_lock"]["reason"] == "disallowed_ast_change"
        assert isinstance(candidate["ast_lock"]["node_kind"], str)
        assert candidate["ast_lock"]["line"] == 1
    assert spec.read_text() == original


def test_failed_memory_candidate_falls_back_to_llm_without_spending_a_loop(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    monkeypatch.setattr(settings, "verify_selectors", False)
    spec = tmp_path / "t.spec.ts"
    spec.write_text(ORIGINAL)
    raw_log = "Error: waiting for locator('#old') timed out"
    parsed_log = parse_error_log(raw_log)
    memory_instruction = PatchInstruction(
        line=1,
        original=ORIGINAL.strip(),
        replacement="await page.click('#memory')",
        reason="previous repair",
        selector="#memory",
    )
    record = make_record(
        error_log=parsed_log,
        instructions=[memory_instruction],
        test_script_path=str(spec),
        source="llm",
    )
    assert record is not None
    assert append_record(record) is True
    llm_calls = 0

    def generate_llm_patch(system_prompt, user_prompt):
        nonlocal llm_calls
        llm_calls += 1
        return _patch_output()

    monkeypatch.setattr(diagnoser_node, "generate_diagnosis", lambda s, u: "selector changed")
    monkeypatch.setattr(patch_node, "generate_patch", generate_llm_patch)
    monkeypatch.setattr(
        runner_node,
        "run_playwright",
        lambda path: ("#memory" not in open(path).read(), "Error: still failing"),
    )
    state = _initial_state()
    state["test_script_path"] = str(spec)
    state["error_log"] = parsed_log

    final = build_graph().invoke(state)

    assert final["is_success"] is True
    assert final["loop_count"] == 0
    assert llm_calls == 1
    assert spec.read_text() == FIXED


# --- a declined patch is a signal, not something to re-test ---------------------------

_DOM_CHANGE = [{"file": "src/App.tsx", "line": 22, "previous": {}, "current": {}}]
_DIAGNOSIS = "The form's submit handler was removed, so the confirmation never renders."


def test_a_declined_patch_judged_a_behaviour_change_refuses_without_rerunning_the_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    spec = tmp_path / "t.spec.ts"
    spec.write_text(ORIGINAL)

    monkeypatch.setattr(diagnoser_node, "generate_diagnosis", lambda s, u: _DIAGNOSIS)
    monkeypatch.setattr(patch_node, "generate_patch", lambda s, u: _declined_output())
    monkeypatch.setattr(
        adjudicator_node,
        "generate_diagnosis_verdict",
        lambda s, u: make_verdict(DiagnosisVerdict.BEHAVIOUR_CHANGE),
    )
    # The candidate changed nothing, so a test run could only repeat the previous answer.
    monkeypatch.setattr(
        runner_node,
        "run_playwright",
        lambda path: (_ for _ in ()).throw(AssertionError("an unchanged test must not be re-run")),
    )

    state = _initial_state()
    state["test_script_path"] = str(spec)
    state["dom_diff_context"] = list(_DOM_CHANGE)
    final = build_graph().invoke(state)

    assert final["is_success"] is False
    assert final["refusal_reason"] is RefusalReason.LIKELY_PRODUCT_REGRESSION
    # The loop the skipped test run would have spent, so the cap still bounds the work.
    assert final["loop_count"] == 1
    assert spec.read_text() == ORIGINAL
    # The decline is still recorded as a candidate, with nothing in it.
    assert len(final["evidence_candidates"]) == 1
    assert final["evidence_candidates"][0]["instructions"] == []
    assert final["evidence_history"][-1]["stage"] == "refusal_finalizer"
    assert final["evidence_history"][-1]["details"]["reason"] == "likely_product_regression"


def test_an_unclear_verdict_keeps_the_loop_and_asks_only_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    spec = tmp_path / "t.spec.ts"
    spec.write_text(ORIGINAL)
    asked: list[int] = []
    runs: list[int] = []

    def adjudicate(system_prompt: str, user_prompt: str) -> DiagnosisVerdictOutput:
        asked.append(1)
        return make_verdict(DiagnosisVerdict.UNCLEAR)

    def run(path: str) -> tuple[bool, str]:
        runs.append(1)
        return False, "Error: still failing"

    monkeypatch.setattr(diagnoser_node, "generate_diagnosis", lambda s, u: _DIAGNOSIS)
    monkeypatch.setattr(patch_node, "generate_patch", lambda s, u: _declined_output())
    monkeypatch.setattr(adjudicator_node, "generate_diagnosis_verdict", adjudicate)
    monkeypatch.setattr(runner_node, "run_playwright", run)

    state = _initial_state()
    state["test_script_path"] = str(spec)
    state["dom_diff_context"] = list(_DOM_CHANGE)
    final = build_graph().invoke(state)

    assert final["refusal_reason"] is RefusalReason.LOOP_CAP_REACHED
    # Nothing changed between cycles, so the same judgment is never bought twice.
    assert len(asked) == 1
    assert len(runs) == settings.max_loops


def test_a_behaviour_change_verdict_cannot_outrank_a_missing_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    spec = tmp_path / "t.spec.ts"
    spec.write_text(ORIGINAL)

    monkeypatch.setattr(diagnoser_node, "generate_diagnosis", lambda s, u: _DIAGNOSIS)
    monkeypatch.setattr(patch_node, "generate_patch", lambda s, u: _declined_output())
    monkeypatch.setattr(
        adjudicator_node,
        "generate_diagnosis_verdict",
        lambda s, u: make_verdict(DiagnosisVerdict.BEHAVIOUR_CHANGE),
    )
    monkeypatch.setattr(runner_node, "run_playwright", lambda path: (False, "Error: failing"))

    state = _initial_state()  # dom_diff_context stays empty
    state["test_script_path"] = str(spec)
    final = build_graph().invoke(state)

    assert final["refusal_reason"] is RefusalReason.INSUFFICIENT_EVIDENCE


def test_a_provider_failure_is_never_adjudicated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.chdir(tmp_path)
    spec = tmp_path / "t.spec.ts"
    spec.write_text(ORIGINAL)

    def adjudicate(system_prompt: str, user_prompt: str) -> DiagnosisVerdictOutput:
        raise AssertionError("an outage must not be judged as a product regression")

    monkeypatch.setattr(diagnoser_node, "generate_diagnosis", lambda s, u: _DIAGNOSIS)
    monkeypatch.setattr(
        patch_node,
        "generate_patch",
        lambda s, u: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )
    monkeypatch.setattr(adjudicator_node, "generate_diagnosis_verdict", adjudicate)
    monkeypatch.setattr(runner_node, "run_playwright", lambda path: (False, "Error: failing"))

    state = _initial_state()
    state["test_script_path"] = str(spec)
    state["dom_diff_context"] = list(_DOM_CHANGE)
    final = build_graph().invoke(state)

    assert final["refusal_reason"] is RefusalReason.PROVIDER_ERROR
