"""Error Log Parser: distill a full Playwright log into the core failure reason."""

import re

import structlog

logger = structlog.get_logger(__name__)

# The `Error:` line carrying the core reason (e.g. `Error: locator.click: Timeout ...`).
_ERROR_RE = re.compile(r"^\s*(Error:.*)$", re.MULTILINE)
# A test-file source location, e.g. `tests/example.spec.ts:12:34`.
_LOCATION_RE = re.compile(r"([\w./-]+\.(?:spec|test)\.[jt]sx?):(\d+):(\d+)")
# Playwright call-log lines, e.g. `- waiting for locator('#submit-btn')`.
_WAITING_RE = re.compile(r"waiting for (.+)")


def parse_error_log(raw_log: str) -> str:
    """Extract only the essentials from a raw Playwright failure log.

    Keeps the ``Error:`` reason, the failing source location, and up to three call-log
    ``waiting for ...`` lines. Falls back to the trimmed tail of the log if nothing
    matches, so the Diagnoser always gets *something* to work with.
    """
    parts: list[str] = []

    error_match = _ERROR_RE.search(raw_log)
    if error_match:
        parts.append(error_match.group(1).strip())

    loc_match = _LOCATION_RE.search(raw_log)
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
