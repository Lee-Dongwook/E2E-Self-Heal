"""Shared Playwright execution helper.

Used both by the CLI's initial failure capture and by the Test Runner node, so the
subprocess invocation lives in exactly one place.
"""

import shlex
import subprocess

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


def run_playwright(test_path: str) -> tuple[bool, str]:
    """Run Playwright against a single test file.

    Returns ``(passed, combined_log)`` where stdout and stderr are merged so the
    Error Log Parser sees the full failure output.
    """
    cmd = [*shlex.split(settings.playwright_cmd), test_path]
    logger.info("playwright_run_started", cmd=cmd)
    result = subprocess.run(cmd, capture_output=True, text=True)
    passed = result.returncode == 0
    log = result.stdout + result.stderr
    logger.info("playwright_run_finished", passed=passed, returncode=result.returncode)
    return passed, log
