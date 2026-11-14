"""Diagnoser node: infer why the test broke."""

import structlog

from app.config import settings
from app.evidence_boundary import (
    PreparedEvidence,
    prepare_evidence,
    redaction_state_update,
)
from app.llm import generate_diagnosis
from app.preprocess.jsx_chunker import chunk_for_line
from app.prompts.diagnoser import SYSTEM_PROMPT, build_user_prompt
from app.state import AgentState

logger = structlog.get_logger(__name__)


DIAGNOSIS_UNAVAILABLE = (
    "Diagnosis unavailable: the diagnosis provider call failed. "
    "Proceed with the raw error log and DOM changes when generating a patch."
)


def build_diagnosis_prompt(prepared: PreparedEvidence, current_code: str) -> str:
    """Assemble the bounded context the Diagnoser asks the model about.

    Shared with the decline adjudicator so both see exactly the same evidence. The evidence
    is already redacted by ``prepare_evidence``; only the test source is passed through as-is,
    because the Patch Generator's instructions must match it byte for byte.
    """
    chunk = chunk_for_line(
        current_code,
        prepared.error_line,
        margin=settings.jsx_chunk_margin_lines,
    )
    return build_user_prompt(
        prepared.error_log,
        prepared.dom_diff_context,
        prepared.aria_snapshot,
        chunk,
        prepared.untouched_selector,
    )


def diagnoser(state: AgentState) -> dict:
    """Map the failing selector to the DOM change and produce an ``analysis_report``.

    On LLM/provider failure, log and return a degraded but valid report rather than crashing
    the graph, so the Router can still advance or terminate gracefully.
    """
    logger.info("diagnoser_started", loop_count=state["loop_count"])
    prepared = prepare_evidence(state)
    try:
        user_prompt = build_diagnosis_prompt(prepared, state["current_code"])
        report = generate_diagnosis(SYSTEM_PROMPT, user_prompt)
    except Exception:
        logger.exception("diagnosis_failed")
        return {
            "analysis_report": DIAGNOSIS_UNAVAILABLE,
            **redaction_state_update(state, prepared),
        }
    logger.info("diagnoser_finished")
    return {"analysis_report": report, **redaction_state_update(state, prepared)}
