"""LangGraph node: memory-first patch attempt from healing history.

On a new failure, look up similar past patterns and attempt an instant first-pass
patch without an LLM call. Falls back to the normal Diagnoser path on a miss.
"""

from __future__ import annotations

import structlog

from app.config import settings
from app.memory.schemas import HealingRecord, MemoryReport
from app.memory.store import get_default_store
from app.nodes.patch_generator import _apply, PatchApplicationError
from app.schemas import PatchInstruction
from app.state import AgentState

logger = structlog.get_logger(__name__)


def _extract_broken_selector(error_log: str) -> str:
    """Best-effort extraction of the failing selector from the error log.

    Tries to find the most common Playwright selector patterns in timeout /
    strict-mode / not-found errors.
    """
    import re
    # Look for patterns like "waiting for locator('#foo')" or "locator('#foo')"
    patterns = [
        r"locator\(['\"](.+?)['\"]\)",
        r"getByRole\(['\"](.+?)['\"]",
        r"getByText\(['\"](.+?)['\"]",
        r"getByTestId\(['\"](.+?)['\"]",
        r"#([a-zA-Z0-9_-]+)",
        r"\[data-testid=['\"](.+?)['\"]\]",
    ]
    for pat in patterns:
        m = re.search(pat, error_log)
        if m:
            return m.group(0)  # return the full match including wrapper
    return ""


def _extract_error_signature(error_log: str) -> str:
    """Normalize the first meaningful line of the error log into a signature."""
    lines = [ln.strip() for ln in error_log.splitlines() if ln.strip()]
    if not lines:
        return ""
    # Prefer the first line that looks like an error (contains Error, timeout, etc.)
    for ln in lines:
        if any(kw in ln.lower() for kw in ("error", "timeout", "failed", "not found", "strict")):
            return ln
    return lines[0]


def memory_lookup(state: AgentState) -> dict:
    """Attempt a memory-first patch before engaging the LLM.

    Returns a partial state update. On a confident hit the patched ``current_code``
    is returned along with a ``memory_report`` so downstream routing can send the
    state straight to the Test Runner. On a miss the code is untouched and routing
    sends it to the Diagnoser.
    """
    if not getattr(settings, "memory_enabled", True):
        logger.info("memory_lookup_skipped", reason="disabled")
        return {"memory_report": MemoryReport(hit=False, error="memory disabled").model_dump()}

    error_signature = _extract_error_signature(state["error_log"])
    broken_selector = _extract_broken_selector(state["error_log"])
    framework = state.get("detected_framework", "")

    logger.info(
        "memory_lookup_started",
        error_signature=error_signature,
        broken_selector=broken_selector,
        framework=framework,
    )

    store = get_default_store()
    matches = store.query(error_signature, broken_selector, framework)

    if not matches:
        logger.info("memory_miss", reason="no_candidates")
        return {
            "memory_report": MemoryReport(
                hit=False, confidence=0.0, error="no matching healing history found"
            ).model_dump()
        }

    best = matches[0]
    record = best.record
    logger.info(
        "memory_hit",
        record_id=record.id,
        confidence=best.confidence,
        broken_selector=record.broken_selector,
        fixed_selector=record.fixed_selector,
    )

    # Replay the stored patch instructions
    instructions = [PatchInstruction(**i) for i in record.patch_instructions]
    try:
        patched = _apply(state["current_code"], instructions)
    except PatchApplicationError as exc:
        logger.warning(
            "memory_hit_patch_application_failed",
            record_id=record.id,
            error=str(exc),
        )
        return {
            "memory_report": MemoryReport(
                hit=False,
                confidence=best.confidence,
                matched_record_id=record.id,
                error=f"matched but patch could not be applied: {exc}",
            ).model_dump()
        }

    logger.info(
        "memory_hit_patch_applied",
        record_id=record.id,
        instruction_count=len(instructions),
    )
    return {
        "current_code": patched,
        "patch_instructions": {
            "instructions": [i.model_dump() for i in instructions],
            "from_memory": True,
            "memory_record_id": record.id,
        },
        "memory_report": MemoryReport(
            hit=True,
            confidence=best.confidence,
            matched_record_id=record.id,
        ).model_dump(),
        "boundary_report": {"ok": True},
        "patch_application_report": {"ok": True},
    }


def memory_save(state: AgentState) -> None:
    """Persist a successful repair into the healing-history store.

    Call this after the Test Runner passes so future failures can benefit from
    the learned pattern.
    """
    if not getattr(settings, "memory_enabled", True):
        return

    instructions = state.get("patch_instructions", {})
    if not instructions or not instructions.get("instructions"):
        return
    # Skip saving if this patch itself came from memory (avoid circular amplification)
    if instructions.get("from_memory"):
        return

    # Derive broken vs fixed selector from the first instruction
    instr_list = instructions.get("instructions", [])
    if not instr_list:
        return

    first = instr_list[0]
    broken_selector = first.get("original", "")
    fixed_selector = first.get("selector", "") or first.get("replacement", "")

    error_signature = _extract_error_signature(state.get("error_log", ""))

    record = HealingRecord(
        test_script_path=state["test_script_path"],
        framework=state.get("detected_framework", ""),
        error_signature=error_signature,
        broken_selector=broken_selector,
        fixed_selector=fixed_selector,
        patch_instructions=instr_list,
        dom_diff_summary="",  # could be enriched later from dom_diff_context
        loop_count=state.get("loop_count", 0),
    )

    store = get_default_store()
    store.save(record)
    logger.info("memory_record_saved_after_success", record_id=record.id)
