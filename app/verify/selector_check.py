"""Verify candidate selectors against the live DOM via a Node/Playwright helper.

The helper is generated as a temporary ``.mjs`` and run with ``node`` as a subprocess —
the same "shell out to the installed Playwright" pattern the Test Runner uses, so no new
Python browser dependency is introduced. It loads the app URL and reports how many elements
each candidate selector resolves to; the caller treats "exactly one" as verified.
"""

import json
import os
import subprocess
import uuid
from pathlib import Path

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.sandbox import assert_command_allowed, assert_write_allowed

logger = structlog.get_logger(__name__)

# Node ESM helper: argv = [url, selectorsJson]; prints {selector: count} to stdout.
# count is -1 when the selector string is invalid for the Playwright engine.
_HELPER_SCRIPT = """
import { chromium } from '@playwright/test';

const url = process.argv[2];
const selectors = JSON.parse(process.argv[3]);

const browser = await chromium.launch();
try {
    const page = await browser.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded' });

    const counts = {};
    for (const selector of selectors) {
        try {
            counts[selector] = await page.locator(selector).count();
        } catch (err) {
            counts[selector] = -1;
        }
    }
    process.stdout.write(JSON.stringify(counts));
} finally {
    await browser.close();
}
"""

# Use a unique suffix to prevent concurrent runs from colliding
_HELPER_FILENAME_PREFIX = ".e2e-healer-verify-"


def _get_helper_path() -> Path:
    """Generate a unique helper path in cwd to prevent concurrent run collisions."""
    # Include PID and random suffix to ensure uniqueness across concurrent runs
    unique_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    return Path.cwd() / f"{_HELPER_FILENAME_PREFIX}{unique_id}.mjs"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
def _run_helper(url: str, selectors: list[str]) -> dict[str, int]:
    """Run the Node helper once and parse its JSON output. Retries on transient failure."""
    script_path = _get_helper_path()

    # Verify the path is allowed by sandbox
    assert_write_allowed(script_path, reason="selector_verifier_helper")

    try:
        # Write the helper script (use write_text for simplicity - atomic not critical here)
        script_path.write_text(_HELPER_SCRIPT)

        cmd = [settings.node_cmd, str(script_path), url, json.dumps(selectors)]
        assert_command_allowed(cmd, reason="selector_verifier")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
        )
    finally:
        # Clean up only our specific helper file
        # Use missing_ok=True to handle race conditions
        try:
            script_path.unlink(missing_ok=True)
        except (PermissionError, OSError) as e:
            # On Windows, unlinking an in-use file raises PermissionError.
            # Log but don't fail - the file will be cleaned up by the OS or
            # left behind (which is acceptable for a temp file).
            logger.warning(
                "selector_helper_cleanup_failed",
                path=str(script_path),
                error=str(e),
            )

    if result.returncode != 0:
        logger.warning(
            "selector_helper_nonzero", returncode=result.returncode, stderr=result.stderr[:500]
        )
        raise RuntimeError("selector_helper_failed")

    return json.loads(result.stdout)


def check_selectors(url: str, selectors: list[str]) -> dict[str, int] | None:
    """Return `{selector: match_count}` for each candidate, or `None` if it can't run.

    `None` signals a graceful skip (Node/Playwright missing, page unreachable, bad JSON):
    verification degrades to "unverified" so the repair loop is never blocked by tooling.
    """
    if not selectors:
        return {}

    try:
        counts = _run_helper(url, selectors)
    except Exception:
        logger.exception("selector_check_skipped")
        return None

    logger.info("selector_check_finished", counts=counts)
    return counts
