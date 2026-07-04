"""Diagnoser node: infer why the test broke."""

import json

import structlog

from app.llm import generate_diagnosis
from app.prompts.diagnoser import SYSTEM_PROMPT
from app.state import AgentState

logger = structlog.get_logger(__name__)


def diagnoser(state: AgentState) -> dict:
    """Map the failing selector to the DOM change and produce an ``analysis_report``."""
    logger.info("diagnoser_started", loop_count=state["loop_count"])
    user_prompt = (
        f"Error log:\n{state['error_log']}\n\n"
        f"DOM changes (from git diff):\n{json.dumps(state['dom_diff_context'], indent=2)}\n\n"
        f"Current test code:\n{state['current_code']}"
    )
    report = generate_diagnosis(SYSTEM_PROMPT, user_prompt)
    logger.info("diagnoser_finished")
    return {"analysis_report": report}
