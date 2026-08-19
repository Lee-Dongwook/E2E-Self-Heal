"""Patch Generator node: produce a narrow, schema-constrained fix."""

import re
from enum import Enum, auto
from pathlib import Path
from typing import cast

import structlog

from app.llm import generate_patch
from app.prompts.patch_generator import (
    DomDiffEntry,
    build_system_prompt,
    detect_framework,
)
from app.sandbox import SandboxViolation, assert_patch_boundary_allowed
from app.schemas import PatchInstruction
from app.state import AgentState
from app.utils.files import split_line_ending

logger = structlog.get_logger(__name__)
_ALLOWED_PATCH_CALL = re.compile(
    r"(?:\bpage\.|\.)"
    r"(?:locator|getByRole|getByText|getByLabel|getByPlaceholder|getByAltText|"
    r"getByTitle|getByTestId|click|dblclick|fill|type|check|uncheck|selectOption|"
    r"setInputFiles|press|hover|focus|waitFor[A-Za-z]*)\s*\("
)
_ASSERTION_CALL = re.compile(r"(?:\b(?:expect|assert)\s*\(|\.(?:toBe|toHave|toEqual)\w*\s*\()")
# "Action" calls: Playwright methods whose selector is the only editable argument —
# data values (fill/type/...) and options (click/hover/...) must stay byte-for-byte.
_ACTION_CALL = re.compile(
    r"(?:\bpage\.)?(fill|type|selectOption|setInputFiles|press|"
    r"click|dblclick|check|uncheck|hover|focus)\s*\("
)
_SELECTOR_CALL = re.compile(
    r"(?:\bpage\.|\.)(locator|getByRole|getByText|getByLabel|getByPlaceholder|"
    r"getByAltText|getByTitle|getByTestId)\s*\("
)


class _MaskState(Enum):
    """Lexical states for :func:`_mask_js_non_code` (Issue #261)."""

    CODE = auto()
    LINE_COMMENT = auto()
    BLOCK_COMMENT = auto()
    TEMPLATE = auto()
    TEMPLATE_EXPR = auto()
    SINGLE = "'"
    DOUBLE = '"'


# Quote characters map onto their masking states; ``TEMPLATE`` is a backtick template.
_QUOTE_STATE = {"'": _MaskState.SINGLE, '"': _MaskState.DOUBLE, "`": _MaskState.TEMPLATE}
# String-literal states only — ``TEMPLATE`` gets its own backtick/${…} handling.
_QUOTE_STATES = frozenset({_MaskState.SINGLE, _MaskState.DOUBLE})


def _matching_paren(text: str, opening: int) -> int | None:
    """Return the matching parenthesis, ignoring quoted JavaScript strings."""
    depth = 0
    quote = ""
    escaped = False
    for index in range(opening, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _argument_spans(text: str, opening: int, closing: int) -> list[tuple[int, int]]:
    """Find top-level argument spans in a JavaScript call."""
    arguments: list[tuple[int, int]] = []
    start = opening + 1
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, closing + 1):
        char = text[index] if index < closing else ","
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            left = start
            while left < index and text[left].isspace():
                left += 1
            right = index
            while right > left and text[right - 1].isspace():
                right -= 1
            if left != right:
                arguments.append((left, right))
            start = index + 1
    return arguments


def _mask_js_non_code(text: str) -> str:
    """Blank JS comments and string/template literals, preserving length and offsets.

    ``_ACTION_CALL``/``_SELECTOR_CALL`` must only see real code — an action-like token
    inside a comment or string must not be treated as an actual call. Line (``//``) and
    block (``/* … */``) comments, single/double-quoted strings, and backtick templates
    (including delimiters and ``\\`` escapes) are replaced with spaces, so matches keep
    the same offsets while the original ``text`` is still used for paren/argument parsing.

    A small state machine drives the masking. Template ``${…}`` expressions are tracked
    (not treated as plain template text) so a nested backtick or ``}`` inside
    interpolation cannot end the template early; the expression is blanked together with
    the literal segments, keeping the guardrail conservative (Issue #261).
    """
    chars = list(text)
    n = len(chars)
    i = 0
    # Lexical context stack: comments, strings, templates, and template expressions
    # return to the context they started from (code or an enclosing expression), so e.g.
    # ``${`b`}`` cannot terminate the surrounding template. ``TEMPLATE_EXPR`` entries
    # carry the ``${`` brace depth; depth 1 closes the expression.
    stack: list[tuple[_MaskState, int]] = [(_MaskState.CODE, 0)]
    while i < n:
        state, depth = stack[-1]
        char = chars[i]
        nxt = chars[i + 1] if i + 1 < n else ""

        if state is _MaskState.CODE:
            if char == "/" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                stack.append((_MaskState.LINE_COMMENT, 0))
            elif char == "/" and nxt == "*":
                chars[i] = chars[i + 1] = " "
                i += 2
                stack.append((_MaskState.BLOCK_COMMENT, 0))
            elif char in {"'", '"', "`"}:
                chars[i] = " "
                i += 1
                stack.append((_QUOTE_STATE[char], 0))
            else:
                i += 1

        elif state is _MaskState.LINE_COMMENT:
            if char == "\n":
                stack.pop()  # the newline is code; everything before it stays blanked
            else:
                chars[i] = " "
            i += 1

        elif state is _MaskState.BLOCK_COMMENT:
            if char == "*" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                stack.pop()
            else:
                chars[i] = " "
                i += 1

        elif state in _QUOTE_STATES:
            if char == "\\":
                chars[i] = " "
                i += 1
                if i < n:
                    chars[i] = " "
                    i += 1
            elif char == state.value:
                chars[i] = " "
                i += 1
                stack.pop()
            else:
                chars[i] = " "
                i += 1

        elif state is _MaskState.TEMPLATE:
            if char == "\\":
                chars[i] = " "
                i += 1
                if i < n:
                    chars[i] = " "
                    i += 1
            elif char == "$" and nxt == "{":
                chars[i] = chars[i + 1] = " "
                i += 2
                stack.append((_MaskState.TEMPLATE_EXPR, 1))
            elif char == "`":
                chars[i] = " "
                i += 1
                stack.pop()
            else:
                chars[i] = " "
                i += 1

        else:  # TEMPLATE_EXPR — ``${…}``: blanked like the template, but tracked so
            # strings, comments, and nested backticks cannot close the expression early.
            if char == "{":
                chars[i] = " "
                i += 1
                stack[-1] = (_MaskState.TEMPLATE_EXPR, depth + 1)
            elif char == "}":
                chars[i] = " "
                i += 1
                if depth == 1:
                    stack.pop()
                else:
                    stack[-1] = (_MaskState.TEMPLATE_EXPR, depth - 1)
            elif char == "/" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                stack.append((_MaskState.LINE_COMMENT, 0))
            elif char == "/" and nxt == "*":
                chars[i] = chars[i + 1] = " "
                i += 2
                stack.append((_MaskState.BLOCK_COMMENT, 0))
            elif char in {"'", '"', "`"}:
                chars[i] = " "
                i += 1
                stack.append((_QUOTE_STATE[char], 0))
            else:
                chars[i] = " "
                i += 1

    return "".join(chars)


def _masked_selector_line(text: str) -> str | None:
    """Mask selector arguments, returning None when an action call cannot be safely checked."""
    spans: list[tuple[int, int]] = []
    code = _mask_js_non_code(text)

    for match in _ACTION_CALL.finditer(code):
        opening = text.find("(", match.start(), match.end())
        closing = _matching_paren(text, opening)
        if closing is None:
            return None
        arguments = _argument_spans(text, opening, closing)
        # page.fill(selector, value) and related page methods take the selector first.
        if text[match.start() :].startswith("page."):
            if not arguments:
                return None
            spans.append(arguments[0])
            continue

        # A locator-bound call may change selectors in its receiver chain, but selector
        # calls inside its value/options arguments are input data and must not be masked.
        receiver_start = code.rfind("page.", 0, match.start())
        if receiver_start == -1:
            continue
        for selector_match in _SELECTOR_CALL.finditer(code, receiver_start, match.start()):
            selector_opening = text.find("(", selector_match.start(), selector_match.end())
            selector_closing = _matching_paren(text, selector_opening)
            if selector_closing is None:
                return None
            if selector_closing < match.start():
                spans.extend(_argument_spans(text, selector_opening, selector_closing))

    if _ACTION_CALL.search(code) and not spans:
        # A locator-bound call has no selector argument of its own. Its data/options
        # arguments must therefore remain byte-for-byte unchanged.
        return text

    masked = text
    for start, end in reversed(sorted(spans)):
        masked = masked[:start] + "<selector>" + masked[end:]
    return masked


def _validate_action_calls(instruction: PatchInstruction) -> None:
    """Allow selector edits while preserving every other argument (data and options)."""
    original = _masked_selector_line(instruction.original)
    replacement = _masked_selector_line(instruction.replacement)
    if original is None or replacement is None or original != replacement:
        raise PatchApplicationError(
            f"line {instruction.line} changes an argument other than the selector "
            "of a Playwright action"
        )


class PatchApplicationError(ValueError):
    """Raised when generated instructions do not match the current test code."""


def _validate_patch_scope(
    instruction: PatchInstruction, masked_original: str | None = None
) -> None:
    """Reject edits outside the single-line locator/wait guardrail.

    ``masked_original`` is the code-only view of the target line computed against the
    *complete* source (see ``_apply``), so a line that continues a block comment or
    template literal opened on an earlier line is treated as non-code. When omitted,
    the line is masked in isolation (tests / callers without file context).
    """
    if "\n" in instruction.replacement or "\r" in instruction.replacement:
        raise PatchApplicationError(
            f"line {instruction.line} replacement must contain exactly one line"
        )
    # Gate on code only: an assertion/locator token inside a comment or string must not
    # satisfy (or trip) the scope checks — the same view `_masked_selector_line` uses.
    original_code = (
        masked_original if masked_original is not None else _mask_js_non_code(instruction.original)
    )
    replacement_code = _mask_js_non_code(instruction.replacement)
    if _ASSERTION_CALL.search(original_code) or _ASSERTION_CALL.search(replacement_code):
        raise PatchApplicationError(f"line {instruction.line} targets an assertion")
    if not _ALLOWED_PATCH_CALL.search(original_code) or not _ALLOWED_PATCH_CALL.search(
        replacement_code
    ):
        raise PatchApplicationError(
            f"line {instruction.line} is not limited to a locator or wait condition"
        )
    if _ACTION_CALL.search(original_code) or _ACTION_CALL.search(replacement_code):
        _validate_action_calls(instruction)


def _apply(code: str, instructions: list[PatchInstruction]) -> str:
    """Validate and atomically apply line-targeted replacements to ``code``."""
    lines = code.splitlines(keepends=True)
    # Mask the complete source once so a targeted line that continues a block comment or
    # template literal opened on an earlier line cannot satisfy the scope gates (Issue #260).
    # The mask preserves length (blanking non-code, including the newlines it swallows), so
    # each original line maps onto the same character span of the masked code.
    masked_code = _mask_js_non_code(code)
    masked_lines: list[str] = []
    offset = 0
    for line in lines:
        masked_lines.append(masked_code[offset : offset + len(line)])
        offset += len(line)
    replacements: list[tuple[int, str]] = []
    targeted_lines: set[int] = set()
    for instruction in instructions:
        index = instruction.line - 1
        if instruction.line in targeted_lines:
            raise PatchApplicationError(f"line {instruction.line} is targeted more than once")
        targeted_lines.add(instruction.line)

        if not 0 <= index < len(lines):
            raise PatchApplicationError(
                f"line {instruction.line} is outside the current file ({len(lines)} line(s))"
            )

        current, line_ending = split_line_ending(lines[index])
        if current != instruction.original:
            raise PatchApplicationError(
                f"line {instruction.line} no longer matches the expected original text"
            )
        masked_current, _ = split_line_ending(masked_lines[index])
        _validate_patch_scope(instruction, masked_original=masked_current)
        replacements.append((index, instruction.replacement + line_ending))

    for index, replacement in replacements:
        lines[index] = replacement
    return "".join(lines)


def patch_generator(state: AgentState) -> dict:
    """Generate a targeted patch via Structured Outputs and apply it to ``current_code``.

    On LLM/parse failure, log and return the code unchanged rather than crashing the
    graph — the Test Runner will fail again and the Router loops until the cap.
    """
    logger.info("patch_generator_started", loop_count=state["loop_count"])
    try:
        assert_patch_boundary_allowed(Path(state["test_script_path"]))
    except SandboxViolation as exc:
        logger.warning(
            "boundary_violation", test_script_path=state["test_script_path"], error=str(exc)
        )
        return {
            "current_code": state["current_code"],
            "patch_instructions": {},
            "analysis_report": state["analysis_report"] + f"\n\n[BOUNDARY FEEDBACK] {exc}",
            "boundary_report": {"ok": False, "error": str(exc)},
            "loop_count": state["loop_count"] + 1,
        }
    user_prompt = (
        f"Failure diagnosis:\n{state['analysis_report']}\n\n"
        f"Current test code:\n{state['current_code']}"
    )
    framework = state.get("detected_framework") or detect_framework(
        state["test_script_path"],
        state["current_code"],
        cast("list[DomDiffEntry]", state["dom_diff_context"]),
    )
    system_prompt = build_system_prompt(framework)
    try:
        output = generate_patch(system_prompt, user_prompt)
    except Exception:
        logger.exception("patch_generation_failed")
        return {
            "current_code": state["current_code"],
            "patch_instructions": {},
            "patch_application_report": {"ok": True},
        }

    try:
        patched = _apply(state["current_code"], output.instructions)
    except PatchApplicationError as exc:
        next_count = state["loop_count"] + 1
        logger.warning("patch_application_rejected", error=str(exc), loop_count=next_count)
        feedback = (
            "\n\n[PATCH APPLICATION FEEDBACK] The previous patch was not applied: "
            f"{exc}. Re-read the current test code and return its exact line text and line number."
        )
        return {
            "current_code": state["current_code"],
            "patch_instructions": {},
            "analysis_report": state["analysis_report"] + feedback,
            "patch_application_report": {"ok": False, "error": str(exc)},
            "loop_count": next_count,
        }
    logger.info("patch_generator_finished", instruction_count=len(output.instructions))
    return {
        "current_code": patched,
        "patch_instructions": output.model_dump(),
        "boundary_report": {"ok": True},
        "patch_application_report": {"ok": True},
    }
