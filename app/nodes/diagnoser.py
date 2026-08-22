"""Diagnoser node: infer why the test broke."""

import structlog

from app.llm import generate_diagnosis
from app.preprocess.aria_snapshot import abstract_snapshot
from app.preprocess.jsx_chunker import chunk_for_line, extract_error_line
from app.prompts.diagnoser import SYSTEM_PROMPT, build_user_prompt
from app.config import settings
from app.state import AgentState

logger = structlog.get_logger(__name__)


_DIAGNOSIS_UNAVAILABLE = (
    "Diagnosis unavailable: the diagnosis provider call failed. "
    "Proceed with the raw error log and DOM changes when generating a patch."
)


def diagnoser(state: AgentState) -> dict:
    """Map the failing selector to the DOM change and produce an ``analysis_report``.

    On any LLM/provider failure, log and return a degraded but valid ``analysis_report``
    rather than crashing the graph (Rule 10) — the Router can then still advance or
    terminate gracefully.
    """
    logger.info("diagnoser_started", loop_count=state["loop_count"])
    snapshot = abstract_snapshot(state.get("dom_snapshot", ""))
    chunk = chunk_for_line(
        state["current_code"],
        extract_error_line(state["error_log"]),
        margin=settings.jsx_chunk_margin_lines,
    )
    user_prompt = build_user_prompt(
        state["error_log"],
        state["dom_diff_context"],
        snapshot,
        chunk,
    )
    try:
        report = generate_diagnosis(SYSTEM_PROMPT, user_prompt)
    except Exception:
        logger.exception("diagnosis_failed")
        return {"analysis_report": _DIAGNOSIS_UNAVAILABLE}
    logger.info("diagnoser_finished")
    return {"analysis_report": report}
