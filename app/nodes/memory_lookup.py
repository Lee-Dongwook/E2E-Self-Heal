"""Healing-history lookup node that reuses a guarded, verified selector repair."""

from pathlib import Path

import structlog

from app.healing_history import find_match
from app.nodes.patch_generator import PatchApplicationError, _apply
from app.sandbox import SandboxViolation, assert_patch_boundary_allowed
from app.schemas import PatchInstruction
from app.state import AgentState

logger = structlog.get_logger(__name__)


def _rebase_instruction(code: str, instruction: PatchInstruction) -> PatchInstruction:
    """Locate the stored original line exactly once in the current test source."""
    lines = code.splitlines()
    matching_lines = [index + 1 for index, line in enumerate(lines) if line == instruction.original]
    if len(matching_lines) != 1:
        raise PatchApplicationError(
            f"stored patch target matched {len(matching_lines)} current source lines; expected exactly one"
        )
    return instruction.model_copy(update={"line": matching_lines[0]})


def memory_lookup(state: AgentState) -> dict:
    """Attempt a high-confidence local repair before invoking any LLM."""
    if not state.get("memory_enabled", True):
        logger.info("memory_disabled")
        return {"memory_report": {"attempted": False, "enabled": False}}
    record, score = find_match(state["error_log"], state["test_script_path"])
    if record is None:
        logger.info("memory_miss", score=score)
        return {"memory_report": {"attempted": True, "hit": False, "score": score}}
    try:
        assert_patch_boundary_allowed(Path(state["test_script_path"]))
        instructions = [
            _rebase_instruction(state["current_code"], item) for item in record.instructions
        ]
        patched = _apply(state["current_code"], instructions)
    except (PatchApplicationError, SandboxViolation) as exc:
        logger.warning("memory_candidate_rejected", error=str(exc), score=score)
        return {
            "memory_report": {
                "attempted": True,
                "hit": False,
                "score": score,
                "rejection": str(exc),
            }
        }
    logger.info("memory_hit", score=score, source=record.source)
    return {
        "current_code": patched,
        "patch_instructions": {"instructions": [item.model_dump() for item in instructions]},
        "boundary_report": {"ok": True},
        "patch_application_report": {"ok": True},
        "memory_report": {
            "attempted": True,
            "hit": True,
            "active": True,
            "score": score,
            "source": "memory",
        },
    }
