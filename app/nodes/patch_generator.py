"""Patch Generator node: produce a narrow, schema-constrained fix."""

import re
from enum import Enum, auto
from pathlib import Path
from typing import cast

import structlog

from app.evidence import add_candidate, add_loop_event
from app.evidence_boundary import prepare_evidence, redaction_state_update
from app.llm import generate_patch
from app.prompts.diagnoser import untouched_selector_fact
from app.prompts.patch_generator import (
    DomDiffEntry,
    build_system_prompt,
    detect_framework,
)
from app.sandbox import SandboxViolation, assert_patch_boundary_allowed
from app.schemas import PatchInstruction
from app.state import AgentState
from app.utils.files import split_line_ending
from app.utils.js_line import restyle_like, same_line_ignoring_format
from app.verify.ast_lock import AstLockVerdict, check_ast_lock

logger = structlog.get_logger(__name__)
_ALLOWED_PATCH_CALL = re.compile(
    r"(?:\bpage\.|\.)"
    r"(?:locator|getByRole|getByText|getByLabel|getByPlaceholder|getByAltText|"
    r"getByTitle|getByTestId|click|dblclick|fill|type|check|uncheck|selectOption|"
    r"setInputFiles|press|hover|focus|waitFor[A-Za-z]*)\s*\("
)
_ASSERTION_CALL = re.compile(
    r"(?:\b(?:expect|assert)(?:\.[A-Za-z_$][\w$]*)?\s*\(|"
    r"\.(?:(?:toBe|toHave|toEqual|toMatch|toContain|toThrow)\w*|"
    r"(?:resolves|rejects)(?:\.[A-Za-z_$][\w$]*)*)\s*\()"
)
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
# A trailing ``{ timeout: <number> }`` argument is a wait condition, not action data, so an
# action may gain, lose, or retune one. The AST lock re-checks the same rule structurally.
_TIMEOUT_ONLY_OPTION = re.compile(r"(?:,\s*)?\{\s*timeout\s*:\s*\d[\d_]*\s*\}\s*(?=\))")


class _MaskState(Enum):
    """Lexical states for :func:`_mask_js_non_code`."""

    CODE = auto()
    LINE_COMMENT = auto()
    BLOCK_COMMENT = auto()
    TEMPLATE = auto()
    TEMPLATE_EXPR = auto()
    REGEX = auto()
    REGEX_CLASS = auto()
    SINGLE = "'"
    DOUBLE = '"'


# Quote characters map onto their masking states; ``TEMPLATE`` is a backtick template.
_QUOTE_STATE = {"'": _MaskState.SINGLE, '"': _MaskState.DOUBLE, "`": _MaskState.TEMPLATE}
# String-literal states only — ``TEMPLATE`` gets its own backtick/${…} handling.
_QUOTE_STATES = frozenset({_MaskState.SINGLE, _MaskState.DOUBLE})

# Characters after which ``/`` is division rather than a regex literal.
_REGEX_DIVISION_AFTER = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$'\"`)]/"
)


def _regex_can_start(prev: str) -> bool:
    """Heuristic: is ``/`` at the current point a regex literal rather than division?

    Mirrors the usual JS-lexer rule: a slash starts a regex at the start of an
    expression or after an operator/bracket/punctuation, while it is division after a
    value (identifier, number, string, closing paren/bracket, another slash). This is
    intentionally not a full tokenizer — the few ambiguous keyword cases (e.g.
    ``return /re/``) may mask slightly less precisely, which errs on the guardrail's
    conservative side.
    """
    return prev not in _REGEX_DIVISION_AFTER


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
    the literal segments, keeping the guardrail conservative. Regex literals are masked
    too (escapes and character classes included), so a ``}`` or ``/`` inside one cannot
    leak into the surrounding expression or end it early.
    """
    chars = list(text)
    n = len(chars)
    i = 0
    # Lexical context stack: comments, strings, templates, and template expressions
    # return to the context they started from (code or an enclosing expression), so e.g.
    # ``${`b`}`` cannot terminate the surrounding template. ``TEMPLATE_EXPR`` entries
    # carry the ``${`` brace depth; depth 1 closes the expression.
    stack: list[tuple[_MaskState, int]] = [(_MaskState.CODE, 0)]
    # Last significant code character, used to tell a regex literal from a division.
    prev = ""
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
            elif char == "/" and _regex_can_start(prev):
                chars[i] = " "
                i += 1
                stack.append((_MaskState.REGEX, 0))
            else:
                if not char.isspace():
                    prev = char
                i += 1

        elif state is _MaskState.LINE_COMMENT:
            if char == "\n":
                stack.pop()  # the newline is code; everything before it stays blanked
                prev = ";"  # a fresh statement may open a regex literal
            else:
                chars[i] = " "
            i += 1

        elif state is _MaskState.BLOCK_COMMENT:
            if char == "*" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                stack.pop()
                prev = ";"
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
                prev = char
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
                prev = ""  # the expression may start with a regex literal
            elif char == "`":
                chars[i] = " "
                i += 1
                stack.pop()
                prev = "`"
            else:
                chars[i] = " "
                i += 1

        elif state is _MaskState.REGEX:
            if char == "\\":
                chars[i] = " "
                i += 1
                if i < n:
                    chars[i] = " "
                    i += 1
            elif char == "[":
                chars[i] = " "
                i += 1
                stack.append((_MaskState.REGEX_CLASS, 0))
            elif char == "/":
                chars[i] = " "
                i += 1
                stack.pop()
                prev = "/"  # a finished regex is a value, so a following / is division
            else:
                chars[i] = " "
                i += 1

        elif state is _MaskState.REGEX_CLASS:
            if char == "\\":
                chars[i] = " "
                i += 1
                if i < n:
                    chars[i] = " "
                    i += 1
            elif char == "]":
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
                prev = "{"
            elif char == "}":
                chars[i] = " "
                i += 1
                if depth == 1:
                    stack.pop()
                else:
                    stack[-1] = (_MaskState.TEMPLATE_EXPR, depth - 1)
                prev = "}"
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
            elif char == "/" and _regex_can_start(prev):
                chars[i] = " "
                i += 1
                stack.append((_MaskState.REGEX, 0))
            else:
                if not char.isspace():
                    prev = char
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


def _validate_action_calls(
    instruction: PatchInstruction,
    *,
    original_source: str | None = None,
    replacement_source: str | None = None,
) -> None:
    """Allow selector edits while preserving every other argument (data and options).

    A complete source view is required for action calls whose arguments continue onto
    another line.  The line-only fallback remains useful for callers that validate an
    instruction before they have the file contents.
    """
    original_text = original_source if original_source is not None else instruction.original
    replacement_text = (
        replacement_source if replacement_source is not None else instruction.replacement
    )
    original = _masked_selector_line(original_text)
    replacement = _masked_selector_line(replacement_text)
    if (
        original is None
        or replacement is None
        or _TIMEOUT_ONLY_OPTION.sub("", original) != _TIMEOUT_ONLY_OPTION.sub("", replacement)
    ):
        raise PatchGuardrailViolation(
            f"line {instruction.line} changes an argument other than the selector "
            "of a Playwright action"
        )


class PatchApplicationError(ValueError):
    """Raised when generated instructions do not match the current test code."""


class PatchGuardrailViolation(PatchApplicationError):
    """Raised when a candidate attempts to leave the selector/wait-condition boundary.

    ``ast_lock`` carries the structural lock's verdict when that check produced the
    rejection, so callers can record what was rejected as structured evidence instead of
    re-parsing the message. It is ``None`` for the per-line scope gates.
    """

    def __init__(self, message: str, *, ast_lock: AstLockVerdict | None = None) -> None:
        super().__init__(message)
        self.ast_lock = ast_lock


def ast_lock_verdict(error: BaseException) -> AstLockVerdict | None:
    """Return the AST lock verdict behind a rejection, or ``None`` if another check raised it."""
    return error.ast_lock if isinstance(error, PatchGuardrailViolation) else None


def _validate_patch_scope(
    instruction: PatchInstruction,
    masked_original: str | None = None,
    *,
    action_original: str | None = None,
    action_replacement: str | None = None,
) -> None:
    """Reject edits outside the single-line locator/wait guardrail.

    ``masked_original`` is the code-only view of the target line computed against the
    *complete* source (see ``_apply``), so a line that continues a block comment or
    template literal opened on an earlier line is treated as non-code. When omitted,
    the line is masked in isolation (tests / callers without file context). Action
    validation additionally receives complete source views from ``_apply`` so a
    multi-line action's arguments can be compared safely.
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
        raise PatchGuardrailViolation(f"line {instruction.line} targets an assertion")
    if not _ALLOWED_PATCH_CALL.search(original_code) or not _ALLOWED_PATCH_CALL.search(
        replacement_code
    ):
        raise PatchGuardrailViolation(
            f"line {instruction.line} is not limited to a locator or wait condition"
        )
    if _ACTION_CALL.search(original_code) or _ACTION_CALL.search(replacement_code):
        _validate_action_calls(
            instruction,
            original_source=action_original,
            replacement_source=action_replacement,
        )


def _enforce_ast_lock(original: str, patched: str) -> None:
    """Reject a patch whose AST differs outside locator strings, timeouts, and wait methods.

    Fail-closed: a parse failure or any structural change is a guardrail violation. It is
    raised as :class:`PatchGuardrailViolation`, a :class:`PatchApplicationError`, so each
    caller handles it through its existing rejection path. The Patch Generator feeds it back
    and spends a loop, so a generated candidate that keeps violating the lock can never retry
    past ``settings.max_loops``. The memory lookup treats it as a cache miss (no loop spent);
    that is bounded because the lookup runs once, before the generator loop starts.
    """
    if patched == original:
        return  # Nothing changed, so there is nothing to verify.
    verdict = check_ast_lock(original, patched)
    if not verdict.allowed:
        logger.warning(
            "ast_lock_rejected",
            reason=verdict.reason,
            node_kind=verdict.node_kind,
            line=verdict.line,
        )
        raise PatchGuardrailViolation(
            f"AST lock rejected the patch ({verdict.reason}: {verdict.node_kind} at line "
            f"{verdict.line}); only locator strings, timeouts, and wait methods may change",
            ast_lock=verdict,
        )
    logger.info("ast_lock_passed", reason=verdict.reason)


def _apply(code: str, instructions: list[PatchInstruction]) -> str:
    """Validate and atomically apply line-targeted replacements to ``code``.

    Shared by the Patch Generator and the memory lookup, so every candidate, however it
    was produced, passes the regex scope gates and then the structural AST lock.
    """
    lines = code.splitlines(keepends=True)
    # Mask the complete source once so a targeted line that continues a block comment or
    # template literal opened on an earlier line cannot satisfy the scope gates.
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
            if not same_line_ignoring_format(current, instruction.original):
                found = [
                    n
                    for n, line in enumerate(lines, 1)
                    if split_line_ending(line)[0] == instruction.original
                    or same_line_ignoring_format(split_line_ending(line)[0], instruction.original)
                ]
                hint = f" (that text is on line {', '.join(map(str, found))})" if found else ""
                raise PatchApplicationError(
                    f"line {instruction.line} no longer matches the expected original text{hint}"
                )
            # The model re-typed the line (quotes, indentation, spacing) instead of copying it
            # (#404). Write its replacement in the file's formatting, and judge that line: the
            # scope gates and the AST lock below see exactly what reaches the disk.
            instruction = instruction.model_copy(
                update={
                    "original": current,
                    "replacement": restyle_like(
                        current, instruction.original, instruction.replacement
                    ),
                }
            )
            logger.info("patch_original_reformatted", line=instruction.line)
        masked_current, _ = split_line_ending(masked_lines[index])
        candidate_lines = lines.copy()
        candidate_lines[index] = instruction.replacement + line_ending
        candidate = "".join(candidate_lines)
        _validate_patch_scope(
            instruction,
            masked_original=masked_current,
            action_original=code,
            action_replacement=candidate,
        )
        replacements.append((index, instruction.replacement + line_ending))

    for index, replacement in replacements:
        lines[index] = replacement
    patched = "".join(lines)
    _enforce_ast_lock(code, patched)
    return patched


def _numbered(code: str) -> str:
    """Prefix each line with its 1-based number so the model need not count lines."""
    return "\n".join(f"{n}| {line}" for n, line in enumerate(code.splitlines(), 1))


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
            "verification_report": {},
            "memory_report": {"active": False, "source": "llm"},
            "loop_count": state["loop_count"] + 1,
            "evidence_history": add_loop_event(
                state, "patch_generator", "boundary_denied", error=str(exc)
            ),
        }
    # The diagnosis can quote the raw log, so it crosses the evidence boundary; the numbered
    # test source does not, because every instruction must match it byte for byte.
    prepared = prepare_evidence(state)
    # The diff check is restated here rather than left to the diagnosis to relay: it is a
    # deterministic fact, and the model must not trade it for a selector it likes better.
    diff_check = (
        f"{untouched_selector_fact(prepared.untouched_selector)}\n\n"
        if prepared.untouched_selector
        else ""
    )
    user_prompt = (
        f"Failure diagnosis:\n{prepared.analysis_report}\n\n"
        f"{diff_check}"
        f"Current test code (each line is prefixed with its 1-based number and '| ', which "
        f"is not part of the line):\n{_numbered(state['current_code'])}"
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
            "patch_provider_report": {"ok": False},
            "verification_report": {},
            "memory_report": {"active": False, "source": "llm"},
            "evidence_history": add_loop_event(state, "patch_generator", "provider_failed"),
            **redaction_state_update(state, prepared),
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
            "patch_application_report": {
                "ok": False,
                "error": str(exc),
                "guardrail_violation": isinstance(exc, PatchGuardrailViolation),
            },
            "patch_provider_report": {"ok": True},
            "verification_report": {},
            "memory_report": {"active": False, "source": "llm"},
            "loop_count": next_count,
            "evidence_candidates": add_candidate(
                state,
                source="llm",
                instructions=output.instructions,
                outcome="rejected",
                rejection=str(exc),
                ast_lock=ast_lock_verdict(exc),
            ),
            "evidence_history": add_loop_event(
                state, "patch_generator", "application_rejected", error=str(exc)
            ),
            **redaction_state_update(state, prepared),
        }
    logger.info("patch_generator_finished", instruction_count=len(output.instructions))
    return {
        "current_code": patched,
        "patch_instructions": output.model_dump(),
        "boundary_report": {"ok": True},
        "patch_application_report": {"ok": True},
        "patch_provider_report": {"ok": True},
        "verification_report": {},
        "memory_report": {"active": False, "source": "llm"},
        "evidence_candidates": add_candidate(state, source="llm", instructions=output.instructions),
        "evidence_history": add_loop_event(
            state,
            "patch_generator",
            "candidate_generated",
            instruction_count=len(output.instructions),
        ),
        **redaction_state_update(state, prepared),
    }
