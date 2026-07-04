"""Patch Generator node: produce a narrow, schema-constrained fix."""

import structlog

from app.llm import generate_patch
from app.prompts.patch_generator import SYSTEM_PROMPT
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
    user_prompt = (
        f"Failure diagnosis:\n{state['analysis_report']}\n\n"
        f"Current test code:\n{state['current_code']}"
    )
    try:
        output = generate_patch(SYSTEM_PROMPT, user_prompt)
    except Exception:
        logger.exception("patch_generation_failed")
        return {"current_code": state["current_code"], "patch_instructions": {}}

    patched = _apply(state["current_code"], output.instructions)
    logger.info("patch_generator_finished", instruction_count=len(output.instructions))
    return {"current_code": patched, "patch_instructions": output.model_dump()}
