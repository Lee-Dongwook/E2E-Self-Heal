from app.nodes.patch_generator import _apply
from app.schemas import PatchInstruction


def _instruction(line: int, replacement: str) -> PatchInstruction:
    return PatchInstruction(line=line, original="", replacement=replacement, reason="test")


def test_replaces_target_line_only():
    code = "line1\nline2\nline3\n"
    result = _apply(code, [_instruction(2, "LINE2")])
    assert result == "line1\nLINE2\nline3\n"


def test_preserves_trailing_newline_state():
    code = "a\nb"  # no trailing newline on last line
    result = _apply(code, [_instruction(2, "B")])
    assert result == "a\nB"


def test_out_of_range_line_is_ignored():
    code = "only\n"
    assert _apply(code, [_instruction(99, "x")]) == "only\n"


def test_empty_instructions_is_noop():
    code = "x\ny\n"
    assert _apply(code, []) == code
