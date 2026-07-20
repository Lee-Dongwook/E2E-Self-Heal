import pytest

import app.nodes.patch_generator as patch_node
from app.graph import route_after_patch
from app.nodes.patch_generator import PatchApplicationError, _apply
from app.schemas import PatchInstruction, PatchOutput
from app.state import AgentState


def _instruction(line: int, original: str, replacement: str) -> PatchInstruction:
    return PatchInstruction(
        line=line,
        original=original,
        replacement=replacement,
        reason="test",
    )


def _state() -> AgentState:
    return {
        "test_script_path": "test.spec.ts",
        "original_code": "line1\nline2\n",
        "current_code": "line1\nline2\n",
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "selector changed",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }


def test_replaces_target_line_only():
    code = "line1\nline2\nline3\n"
    result = _apply(code, [_instruction(2, "line2", "LINE2")])
    assert result == "line1\nLINE2\nline3\n"


def test_preserves_trailing_newline_state():
    code = "a\nb"  # no trailing newline on last line
    result = _apply(code, [_instruction(2, "b", "B")])
    assert result == "a\nB"


def test_preserves_crlf_line_endings():
    code = "a\r\nb\r\n"
    result = _apply(code, [_instruction(2, "b", "B")])
    assert result == "a\r\nB\r\n"


def test_rejects_out_of_range_line():
    with pytest.raises(PatchApplicationError, match="outside the current file"):
        _apply("only\n", [_instruction(99, "missing", "x")])


def test_rejects_mismatched_original_line():
    with pytest.raises(PatchApplicationError, match="no longer matches"):
        _apply("actual\n", [_instruction(1, "stale", "replacement")])


def test_rejects_duplicate_line_targets():
    instructions = [
        _instruction(1, "line1", "first"),
        _instruction(1, "line1", "second"),
    ]
    with pytest.raises(PatchApplicationError, match="targeted more than once"):
        _apply("line1\n", instructions)


def test_rejects_entire_patch_set_when_one_instruction_is_stale():
    instructions = [
        _instruction(1, "line1", "LINE1"),
        _instruction(2, "stale", "LINE2"),
    ]
    with pytest.raises(PatchApplicationError, match="no longer matches"):
        _apply("line1\nline2\n", instructions)


def test_empty_instructions_is_noop():
    code = "x\ny\n"
    assert _apply(code, []) == code


def test_patch_generator_returns_rejection_feedback(monkeypatch):
    output = PatchOutput(
        instructions=[_instruction(2, "stale line", "replacement")],
    )
    monkeypatch.setattr(patch_node, "generate_patch", lambda system, user: output)

    state = _state()
    result = patch_node.patch_generator(state)

    assert result["current_code"] == state["current_code"]
    assert result["patch_instructions"] == {}
    assert result["patch_application_report"]["ok"] is False
    assert result["loop_count"] == 1
    assert "[PATCH APPLICATION FEEDBACK]" in result["analysis_report"]

    state["patch_application_report"] = result["patch_application_report"]
    state["loop_count"] = result["loop_count"]
    assert route_after_patch(state) == "patch_generator"
