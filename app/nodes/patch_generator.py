"""Patch Generator node: produce a narrow, schema-constrained fix."""

from pathlib import Path

import structlog

from app.llm import generate_patch
from app.prompts.patch_generator import build_system_prompt, detect_framework
from app.sandbox import SandboxViolation, assert_patch_boundary_allowed
from app.schemas import PatchInstruction
from app.state import AgentState

logger = structlog.get_logger(__name__)


def _apply(code: str, instructions: list[PatchInstruction]) -> str:
    """Apply line-targeted replacements to ``code`` (1-based line numbers)."""
    lines = code.splitlines(keepends=True)
    for instruction in instructions:
        index = instruction.line - 1
        if 0 <= index < len(lines):
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = instruction.replacement + newline
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
        state["dom_diff_context"],
    )
    system_prompt = build_system_prompt(framework)
    try:
        output = generate_patch(system_prompt, user_prompt)
    except Exception:
        logger.exception("patch_generation_failed")
        return {"current_code": state["current_code"], "patch_instructions": {}}

    patched = _apply(state["current_code"], output.instructions)
    logger.info("patch_generator_finished", instruction_count=len(output.instructions))
    return {
        "current_code": patched,
        "patch_instructions": output.model_dump(),
        "boundary_report": {"ok": True},
    }
