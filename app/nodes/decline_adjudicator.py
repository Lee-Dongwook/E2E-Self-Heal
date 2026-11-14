"""Decline Adjudicator node: decide whether a declined patch means the product changed."""

import structlog

from app.evidence_boundary import prepare_evidence, redaction_state_update
from app.llm import generate_diagnosis_verdict
from app.nodes.diagnoser import DIAGNOSIS_UNAVAILABLE, build_diagnosis_prompt
from app.prompts.decline_adjudicator import SYSTEM_PROMPT, build_user_prompt
from app.schemas import DiagnosisVerdict
from app.state import AgentState

logger = structlog.get_logger(__name__)


def decline_adjudicator(state: AgentState) -> dict:
    """Ask for a structured verdict about a diagnosis that ended in no proposed change.

    A declined candidate leaves ``current_code`` byte-identical, so the verdict is asked at
    most once per run and reused. Only ``BEHAVIOUR_CHANGE`` spends a loop, standing in for the
    test run the graph would otherwise spend on an unchanged file, so ``max_loops`` still
    bounds the work. A provider failure or degraded diagnosis records nothing, leaving the run
    on its existing path instead of turning an outage into a product-regression claim.
    """
    cached = state.get("diagnosis_verdict")
    if cached is not None:
        logger.info("decline_adjudication_cached", verdict=cached.value)
        return {}
    if state["analysis_report"] == DIAGNOSIS_UNAVAILABLE:
        logger.info("decline_adjudication_skipped", reason="diagnosis_unavailable")
        return {}
    prepared = prepare_evidence(state)
    user_prompt = build_user_prompt(
        prepared.analysis_report, build_diagnosis_prompt(prepared, state["current_code"])
    )
    try:
        output = generate_diagnosis_verdict(SYSTEM_PROMPT, user_prompt)
    except Exception:
        logger.exception("decline_adjudication_failed")
        return redaction_state_update(state, prepared)
    logger.info("decline_adjudicated", verdict=output.verdict.value)
    update: dict = {
        "diagnosis_verdict": output.verdict,
        **redaction_state_update(state, prepared),
    }
    if output.verdict is DiagnosisVerdict.BEHAVIOUR_CHANGE:
        update["loop_count"] = state["loop_count"] + 1
    return update
