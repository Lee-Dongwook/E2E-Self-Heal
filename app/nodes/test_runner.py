"""Test Runner node: write the patched test and run Playwright."""

from pathlib import Path

import structlog

from app.preprocess.error_log_parser import parse_error_log
from app.runner import run_playwright
from app.state import AgentState

logger = structlog.get_logger(__name__)


def test_runner(state: AgentState) -> dict:
    """Write ``current_code`` to disk and run Playwright via the shared runner.

    On pass returns ``{"is_success": True}``; on fail returns the re-parsed error log
    and an incremented ``loop_count``.
    """
    path = state["test_script_path"]
    logger.info("test_runner_started", test_script_path=path)
    Path(path).write_text(state["current_code"])

    passed, log = run_playwright(path)
    if passed:
        logger.info("test_runner_passed", loop_count=state["loop_count"])
        return {"is_success": True}

    next_count = state["loop_count"] + 1
    logger.info("test_runner_failed", loop_count=next_count)
    return {
        "is_success": False,
        "error_log": parse_error_log(log),
        "loop_count": next_count,
    }
