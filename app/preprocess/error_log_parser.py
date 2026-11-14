"""Error Log Parser: distill a full Playwright log into the core failure reason."""

import re

import structlog

logger = structlog.get_logger(__name__)

# The `Error:` line carrying the core reason (e.g. `Error: locator.click: Timeout ...`).
_ERROR_RE = re.compile(r"^\s*(Error:.*)$", re.MULTILINE)
# A test-file source location, e.g. `tests/example.spec.ts:12:34`.
_LOCATION_RE = re.compile(r"([\w./-]+\.(?:spec|test)\.[jt]sx?):(\d+):(\d+)")
# The stack-trace failing line, e.g. `at tests/example.spec.ts:12:9` — the line to patch,
# preferred over the test-header location that appears earlier in the log.
_AT_LOCATION_RE = re.compile(r"\bat\s+([\w./-]+\.(?:spec|test)\.[jt]sx?):(\d+):(\d+)")
# Playwright call-log lines, e.g. `- waiting for locator('#submit-btn')`.
_WAITING_RE = re.compile(r"waiting for (.+)")
# The resolved locator that an `expect(...)` assertion targeted, e.g.
# `Locator: getByRole('heading', { name: 'Dashboard' })` — the selector to heal.
_LOCATOR_RE = re.compile(r"^\s*(Locator:\s*\S.*)$", re.MULTILINE)
# Assertion mismatch lines from `expect()` failures, e.g. `Expected: visible` /
# `Received: hidden` (also `Expected string:`, `Received: <element(s) not found>`).
_EXPECTED_RE = re.compile(r"^\s*(Expected(?:\s+\w+)?:\s*\S.*)$", re.MULTILINE)
_RECEIVED_RE = re.compile(r"^\s*(Received(?:\s+\w+)?:\s*\S.*)$", re.MULTILINE)
# Strict-mode multiplicity, e.g. `resolved to 2 elements` — a too-broad selector signal.
# Captured even when it appears outside the `Error:` line (e.g. in the call log).
_RESOLVED_RE = re.compile(r"resolved to \d+ elements?")
# Cap for each captured single-line signal so element dumps can't blow the context budget.
_MAX_LINE_CHARS = 200


def parse_error_log(raw_log: str | None) -> str:
    """Extract only the essentials from a raw Playwright failure log.

    Keeps the ``Error:`` reason; the resolved ``Locator:`` and ``Expected:``/``Received:``
    lines from ``expect()`` assertion failures; any strict-mode ``resolved to N elements``
    signal; the failing source location; and up to three call-log ``waiting for ...``
    lines. Falls back to the trimmed tail of the log if nothing matches, so the Diagnoser
    always gets *something* to work with.
    """
    if not raw_log:
        return ""

    parts: list[str] = []

    error_match = _ERROR_RE.search(raw_log)
    if error_match:
        parts.append(error_match.group(1).strip())

    # `expect()` assertion signals are the strongest healing hints for assertion-style failures.
    for pattern in (_LOCATOR_RE, _EXPECTED_RE, _RECEIVED_RE):
        match = pattern.search(raw_log)
        if match:
            parts.append(match.group(1).strip()[:_MAX_LINE_CHARS])

    # Only added when the `Error:` reason doesn't already carry the phrase.
    resolved_match = _RESOLVED_RE.search(raw_log)
    if resolved_match and not any(resolved_match.group(0) in part for part in parts):
        parts.append(f"strict mode: {resolved_match.group(0)}")

    loc_match = _AT_LOCATION_RE.search(raw_log) or _LOCATION_RE.search(raw_log)
    if loc_match:
        parts.append(f"at {loc_match.group(1)}:{loc_match.group(2)}")

    waiting = [w.strip() for w in _WAITING_RE.findall(raw_log)]
    if waiting:
        parts.append("call log: " + "; ".join(waiting[:3]))

    result = "\n".join(parts)
    if not result:
        logger.warning("error_log_parse_fell_back_to_tail")
        result = raw_log.strip()[-1000:]

    logger.debug("error_log_parsed", chars=len(result))
    return result
