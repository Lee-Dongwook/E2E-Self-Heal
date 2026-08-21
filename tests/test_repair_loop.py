"""End-to-end graph test with the LLM and Playwright mocked out."""

import app.nodes.diagnoser as diagnoser_node
import app.nodes.patch_generator as patch_node
import app.nodes.test_runner as runner_node
from app.config import settings
from app.graph import build_graph
from app.healing_history import append_record, make_record
from app.preprocess.error_log_parser import parse_error_log
from app.schemas import PatchInstruction, PatchOutput
from app.state import AgentState

ORIGINAL = "await page.click('#old')\n"
FIXED = "await page.click('#new')\n"


def _initial_state() -> AgentState:
    return {
        "test_script_path": "",  # set per test to the tmp file
        "original_code": ORIGINAL,
        "current_code": ORIGINAL,
        "error_log": "Error: Timeout\nwaiting for locator('#old')",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }


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
