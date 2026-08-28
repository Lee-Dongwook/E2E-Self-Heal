"""Shared Playwright execution helper.

Used both by the CLI's initial failure capture and by the Test Runner node, so the
subprocess invocation lives in exactly one place.
"""

import os
import shlex
import signal
import subprocess
import sys
from typing import Any

import structlog

from app.config import settings
from app.sandbox import assert_command_allowed

logger = structlog.get_logger(__name__)

# Cleanup commands (e.g. Windows ``taskkill``) should return promptly; cap them so a
# stalled kill cannot block ``run_playwright`` indefinitely.
_TERMINATE_TIMEOUT_SECONDS = 10


def process_group_kwargs() -> dict[str, Any]:
    """Return ``Popen`` options that isolate a child process tree for cleanup."""
    return (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )


def _as_text(stream: str | bytes | None) -> str:
    """Coerce captured subprocess output to text (it is bytes when a timeout kills the run)."""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode(errors="replace")
    return stream


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Kill the process and all its descendants (Playwright browsers, helpers).

    ``subprocess.run`` only kills the immediate child on timeout, which leaves orphaned
    browser and helper processes behind. We launch the child in its own process group /
    session (see ``run_playwright``) so the whole tree can be signalled at once.
    """
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=_TERMINATE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            logger.warning("taskkill_timed_out", pid=process.pid)
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()


def run_playwright(test_path: str = "") -> tuple[bool, str]:
    """Run Playwright against a single test file, or the whole suite if ``test_path`` is empty.

    Returns ``(passed, combined_log)`` where stdout and stderr are merged so the
    Error Log Parser sees the full failure output.
    """
    cmd = [*shlex.split(settings.playwright_cmd), *([test_path] if test_path else [])]
    assert_command_allowed(cmd, reason="playwright")
    timeout = settings.test_timeout_seconds
    logger.info("playwright_run_started", cmd=cmd, timeout=timeout)
    # Launch in its own process group (POSIX) / group (Windows) so a timeout can reap the
    # entire tree — Playwright spawns browser and helper descendants that ``subprocess.run``
    # would otherwise leave orphaned.
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **process_group_kwargs(),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # A hung run (dead dev server, deadlocked waitForSelector, orphaned browser) must not
        # block the repair loop. Kill the whole process tree and surface it as an ordinary
        # test failure so the caller refreshes error_log and increments loop_count — never
        # crash the graph.
        _terminate_process_tree(process)
        try:
            drained_out, drained_err = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            drained_out, drained_err = "", ""
        logger.warning("test_run_timeout", path=test_path, timeout=timeout)
        partial = (
            _as_text(exc.stdout)
            + _as_text(exc.stderr)
            + _as_text(drained_out)
            + _as_text(drained_err)
        )
        timeout_note = f"Error: test run timed out after {timeout}s and was killed."
        log = f"{partial}\n{timeout_note}" if partial else timeout_note
        return False, log

    passed = process.returncode == 0
    log = _as_text(stdout) + _as_text(stderr)
    logger.info("playwright_run_finished", passed=passed, returncode=process.returncode)
    return passed, log
