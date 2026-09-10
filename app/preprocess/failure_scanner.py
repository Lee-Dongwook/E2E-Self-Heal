"""Scan a whole-suite Playwright log for the distinct test files that failed.

Playwright's reporter lists each failure as a numbered entry, optionally prefixed with a
project tag, e.g.::

    1) [chromium] › tests/login.spec.ts:12:3 › user can log in
    2) example.spec.ts:7:5 › submits the form

We extract the failing test-file paths (deduplicated, in first-seen order) so the suite
healer can repair each one.

Security note: these paths come from reporter text the app under test can influence, so
they are UNTRUSTED. Callers must authorize each one with
"""

import re

import structlog

logger = structlog.get_logger(__name__)

# A numbered failure entry; capture the first test-file path on the line. The path character
# class includes spaces, `@`, and `%` so paths like `tests/my suite/login.spec.ts` are not
# truncated to a suffix; `[`, `›`, and `:` still delimit it from the project tag and location.
# A literal space (not ``\s``) keeps the match on one line; the captured path is stripped to
# drop the separator space after a ``[project] ›`` prefix.
_FAILURE_RE = re.compile(
    r"^\s*\d+\)\s+.*?((?:[a-zA-Z]:)?[-\w./\\ @%]+\.(?:spec|test)\.[jt]sx?):\d+",
    re.MULTILINE,
)


def scan_failing_tests(raw_log: str) -> list[str]:
    """Return distinct failing test-file paths from a whole-suite Playwright log."""
    seen: dict[str, None] = {}  # dict preserves first-seen order while deduping
    for match in _FAILURE_RE.finditer(raw_log):
        seen.setdefault(match.group(1).strip(), None)
    paths = list(seen)
    logger.info("failing_tests_scanned", count=len(paths))
    return paths
