"""Selector Verifier node: ground LLM-produced selectors against the live DOM.

Sits between the Patch Generator and the Test Runner. Before spending a full Playwright
run, it checks that each patched selector resolves to exactly one element on the real page.
A hallucinated (0 matches) or ambiguous (>1 matches) selector is rejected: the just-applied
patch is reverted and the loop re-patches with feedback, saving a wasted test run.

Graceful by design — if verification is disabled, no app URL is set, there are no selector
edits, or the Node helper can't run, it passes through and lets the Test Runner arbitrate.
"""

from pathlib import Path

import structlog

from app.config import settings
from app.schemas import PatchInstruction
from app.state import AgentState
from app.utils.files import atomic_write, split_line_ending
from app.verify.selector_check import check_selectors

logger = structlog.get_logger(__name__)

_SKIPPED: dict = {"skipped": True, "ok": True}


def _revert(code: str, instructions: list[PatchInstruction]) -> str:
    """Undo an applied patch by restoring each instruction's original line (1-based)."""
    lines = code.splitlines(keepends=True)
    for instruction in instructions:
        index = instruction.line - 1
        if 0 <= index < len(lines):
            _, line_ending = split_line_ending(lines[index])
            lines[index] = instruction.original + line_ending
    return "".join(lines)


def _feedback(bad: dict[str, int]) -> str:
    """Build a diagnosis addendum naming the rejected selectors for the next patch attempt."""
    detail = "; ".join(f"'{sel}' matched {count} element(s)" for sel, count in bad.items())
    return (
        "\n\n[VERIFICATION FEEDBACK] The previous patch was rejected against the live DOM: "
        f"{detail}. Each selector must resolve to exactly one element — choose a different, "
        "more specific selector and do not reuse the rejected one."
    )


def selector_verifier(state: AgentState) -> dict:
    """Verify patched selectors against the live DOM; revert and loop back if any is invalid."""
    if not settings.verify_selectors or not settings.app_url:
        return {"verification_report": _SKIPPED}

    instructions = [
        PatchInstruction(**i) for i in state["patch_instructions"].get("instructions", [])
    ]
    selectors = [i.selector for i in instructions if i.selector]
    if not selectors:
        return {"verification_report": _SKIPPED}

    logger.info("selector_verify_started", selector_count=len(selectors), url=settings.app_url)
    counts = check_selectors(settings.app_url, selectors)
    if counts is None:
        logger.info("selector_verify_unavailable")  # tooling missing -> defer to Test Runner
        return {"verification_report": _SKIPPED}

    bad = {sel: count for sel, count in counts.items() if count != 1}
    if not bad:
        logger.info("selector_verify_passed", counts=counts)
        return {
            "rollback_code": state["current_code"],
            "verification_report": {"ok": True, "counts": counts},
        }

    rollback_code = state.get("rollback_code", state["original_code"])
    atomic_write(Path(state["test_script_path"]), rollback_code)
    memory_candidate = state.get("memory_report", {}).get("active", False)
    if memory_candidate:
        logger.info("memory_candidate_rejected", stage="selector_verifier", bad=bad)
    next_count = state["loop_count"] if memory_candidate else state["loop_count"] + 1
    logger.info("selector_verify_failed", bad=bad, loop_count=next_count)
    return {
        "current_code": rollback_code,
        "rollback_code": rollback_code,
        "analysis_report": state["analysis_report"] + _feedback(bad),
        "loop_count": next_count,
        "verification_report": {"ok": False, "counts": counts},
    }
