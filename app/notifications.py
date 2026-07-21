"""Slack notifier for heal outcomes (Issue #124)."""

import json
import urllib.error
import urllib.request
from typing import Any, TypedDict

import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings
from app.schemas import RepairSummary

logger = structlog.get_logger(__name__)


class SlackPayload(TypedDict):
    """Type definition for Slack webhook payload."""

    text: str
    blocks: list[dict[str, Any]]


def _is_transient_error(exception: BaseException) -> bool:
    """Determine if an error is transient and should be retried.

    Only retry on:
    - Network errors (URLError, TimeoutError, ConnectionError)
    - HTTP 429 (rate limit) or 5xx (server errors)
    Do NOT retry on 4xx client errors (invalid webhook, auth failures, etc.)
    """
    if isinstance(exception, urllib.error.HTTPError):
        # Only retry rate limits (429) and server errors (5xx)
        return exception.code >= 429
    return isinstance(exception, (urllib.error.URLError, TimeoutError, ConnectionError))


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(_is_transient_error),
    reraise=True,
)
def _post_to_slack(payload: SlackPayload) -> None:
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

    try:
        with urllib.request.urlopen(req, timeout=10.0) as response:
            if response.status >= 400:
                raise urllib.error.HTTPError(
                    url, response.status, response.reason, response.headers, None
                )
    except urllib.error.HTTPError as exc:
        # Re-raise to trigger retry logic for transient errors
        raise exc

    # Log success without exposing the webhook URL
    logger.info("notification_sent", status="success")


def _build_payload(summary: RepairSummary) -> SlackPayload:
    """Build a typed Slack payload from a repair summary."""
    outcome = "✅ Healed" if summary.is_success else "❌ Failed"

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

    return {
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


def notify_heal_outcome(summary: RepairSummary) -> None:
    """Post a concise summary of a heal run to Slack, if configured."""
    if not settings.slack_webhook_url:
        return

    payload = _build_payload(summary)

    try:
        _post_to_slack(payload)
    except Exception as e:
        # Log failure without exposing credentials
        logger.error("notification_failed", error=str(e))
