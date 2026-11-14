"""Reviewer node (review mode): advise a source-level fix instead of patching the test."""

import json

import structlog

from app.evidence_boundary import prepare_evidence, redaction_state_update
from app.llm import generate_review
from app.prompts.reviewer import SYSTEM_PROMPT
from app.state import AgentState

logger = structlog.get_logger(__name__)


def reviewer(state: AgentState) -> dict:
    """Turn the diagnosis + DOM changes into source-level suggestions (never edits the test).

    On provider failure, log and return an incomplete report so the CLI can fail the review
    rather than mistake an outage for a completed review with no findings.
    """
    logger.info("reviewer_started")
    # Diagnostics and DOM changes cross the evidence boundary; the test source does not, because
    # a suggested locator has to match the code the user actually has.
    prepared = prepare_evidence(state)
    user_prompt = (
        f"Failure diagnosis:\n{prepared.analysis_report}\n\n"
        f"DOM changes (from git diff, with new-file lines):\n"
        f"{json.dumps(prepared.dom_diff_context, indent=2)}\n\n"
        f"Current test code:\n{state['current_code']}"
    )
    try:
        output = generate_review(SYSTEM_PROMPT, user_prompt)
    except Exception:
        logger.exception("review_generation_failed")
        return {
            "review_report": {
                "findings": [],
                "is_complete": False,
                "error": "review provider failed to generate suggestions",
            },
            **redaction_state_update(state, prepared),
        }

    logger.info("reviewer_finished", finding_count=len(output.findings))
    return {
        "review_report": output.model_dump(),
        **redaction_state_update(state, prepared),
    }
