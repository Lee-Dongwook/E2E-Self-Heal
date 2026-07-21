"""Slack notifier for heal outcomes (Issue #124)."""

import json
import urllib.error
import urllib.request

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.schemas import RepairSummary

logger = structlog.get_logger(__name__)


def is_transient_error(exception: BaseException) -> bool:
    """Retry only on transient network errors."""
    return isinstance(exception, (urllib.error.URLError, TimeoutError, ConnectionError))


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=is_transient_error,
    reraise=True,
)
def _post_to_slack(payload: dict) -> None:
    """Send a payload to the configured Slack webhook with retry logic."""
    url = settings.slack_webhook_url
    if not url:
        return

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    # We use urllib to avoid adding new dependencies
    with urllib.request.urlopen(req, timeout=10.0) as response:
        if response.status >= 400:
            raise urllib.error.HTTPError(
                url, response.status, response.reason, response.headers, None
            )

    logger.info("notification_sent", url=url)


def notify_heal_outcome(summary: RepairSummary) -> None:
    """Post a concise summary of a heal run to Slack, if configured."""
    if not settings.slack_webhook_url:
        return

    outcome = "✅ Healed" if summary.is_success else "❌ Failed"

    # Extract selector changes (before/after)
    selector_changes = []
    for instr in summary.instructions:
        before = instr.original.strip()
        after = instr.replacement.strip()
        selector_changes.append(f"• *Line {instr.line}:* {instr.reason}")
        selector_changes.append(f"  _Before:_ `{before}`")
        selector_changes.append(f"  _After:_ `{after}`")
        if instr.selector:
            selector_changes.append(f"  _New Selector:_ `{instr.selector}`")

    changes_text = (
        "\n".join(selector_changes) if selector_changes else "_No selector changes recorded_"
    )

    text = (
        f"*{outcome} E2E Self-Healing Run*\n"
        f"*File:* `{summary.test_script_path}`\n"
        f"*Loops:* {summary.loop_count}\n"
        f"*Changes:*\n{changes_text}"
    )

    payload = {
        "text": text,
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{outcome} E2E Self-Healing Run",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*File:*\n`{summary.test_script_path}`"},
                    {"type": "mrkdwn", "text": f"*Loops:*\n{summary.loop_count}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Selector Changes:*\n{changes_text}"},
            },
        ],
    }

    try:
        _post_to_slack(payload)
    except Exception as e:
        # We log the error but don't fail the heal process because of a notification failure
        logger.error("notification_failed", error=str(e))
