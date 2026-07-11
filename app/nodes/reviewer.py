"""Reviewer node (review mode): advise a source-level fix instead of patching the test."""

import json

import structlog

from app.llm import generate_review
from app.prompts.reviewer import SYSTEM_PROMPT
from app.state import AgentState

logger = structlog.get_logger(__name__)


def reviewer(state: AgentState) -> dict:
    """Turn the diagnosis + DOM changes into source-level suggestions (never edits the test).

    On any LLM/parse failure, log and return an empty report rather than crashing the graph
    (Rule 10) — the CLI then reports "no findings" instead of failing the run.
    """
    logger.info("reviewer_started")
    user_prompt = (
        f"Failure diagnosis:\n{state['analysis_report']}\n\n"
        f"DOM changes (from git diff, with new-file lines):\n"
        f"{json.dumps(state['dom_diff_context'], indent=2)}\n\n"
        f"Current test code:\n{state['current_code']}"
    )
    try:
        output = generate_review(SYSTEM_PROMPT, user_prompt)
    except Exception:
        logger.exception("review_generation_failed")
        return {"review_report": {"findings": []}}

    logger.info("reviewer_finished", finding_count=len(output.findings))
    return {"review_report": output.model_dump()}
