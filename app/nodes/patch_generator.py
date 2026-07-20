"""Patch Generator node: produce a narrow, schema-constrained fix."""

from pathlib import Path

import structlog

from app.llm import generate_patch
from app.prompts.patch_generator import SYSTEM_PROMPT
from app.sandbox import SandboxViolation, assert_patch_boundary_allowed
from app.schemas import PatchInstruction
from app.state import AgentState

logger = structlog.get_logger(__name__)


class PatchApplicationError(ValueError):
    """Raised when generated instructions do not match the current test code."""


def _split_line_ending(line: str) -> tuple[str, str]:
    """Return a line's content and exact line ending."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith(("\n", "\r")):
        return line[:-1], line[-1:]
    return line, ""


def _apply(code: str, instructions: list[PatchInstruction]) -> str:
    """Validate and atomically apply line-targeted replacements to ``code``."""
    lines = code.splitlines(keepends=True)
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

        current, line_ending = _split_line_ending(lines[index])
        if current != instruction.original:
            raise PatchApplicationError(
                f"line {instruction.line} no longer matches the expected original text"
            )
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
    try:
        output = generate_patch(SYSTEM_PROMPT, user_prompt)
    except Exception:
        logger.exception("patch_generation_failed")
        return {"current_code": state["current_code"], "patch_instructions": {}}

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
