"""Tests for the Slack notifier (Issue #124)."""

import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from app.notifications import notify_heal_outcome
from app.schemas import PatchInstruction, RepairSummary


def make_summary(
    is_success: bool = True, instructions: list[PatchInstruction] | None = None
) -> RepairSummary:
    return RepairSummary(
        test_script_path="tests/login.spec.ts",
        is_success=is_success,
        loop_count=2,
        instructions=instructions or [],
    )


def test_noop_when_webhook_unset() -> None:
    """Should do nothing if slack_webhook_url is empty."""
    with patch("app.notifications.settings") as mock_settings:
        mock_settings.slack_webhook_url = ""
        with patch("app.notifications.urllib.request.urlopen") as mock_urlopen:
            notify_heal_outcome(make_summary())
            mock_urlopen.assert_not_called()


@patch("app.notifications.urllib.request.urlopen")
def test_posts_payload_when_configured(mock_urlopen: MagicMock) -> None:
    """Should post a correctly formatted payload when webhook is set."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    instr = PatchInstruction(
        line=42,
        original="page.click('#old-btn')",
        replacement="page.click('role=button[name=\"Submit\"]')",
        reason="button id changed",
        selector='role=button[name="Submit"]',
    )
    summary = make_summary(instructions=[instr])

    with patch("app.notifications.settings") as mock_settings:
        mock_settings.slack_webhook_url = "https://hooks.slack.com/services/FAKE"
        notify_heal_outcome(summary)

        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert req.full_url == "https://hooks.slack.com/services/FAKE"

        payload = json.loads(req.data.decode("utf-8"))
        assert "Healed" in payload["text"]
        assert "tests/login.spec.ts" in payload["text"]
        assert "*Loops:* 2" in payload["text"]
        assert "button id changed" in payload["text"]


@patch("app.notifications.urllib.request.urlopen")
def test_failed_outcome(mock_urlopen: MagicMock) -> None:
    """Should post 'Failed' when is_success is False."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    summary = make_summary(is_success=False)

    with patch("app.notifications.settings") as mock_settings:
        mock_settings.slack_webhook_url = "https://hooks.slack.com/services/FAKE"
        notify_heal_outcome(summary)

        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        assert "Failed" in payload["text"]


@patch("app.notifications.urllib.request.urlopen")
def test_retry_on_transient_error(mock_urlopen: MagicMock) -> None:
    """Should retry on URLError and eventually log failure without crashing."""
    mock_urlopen.side_effect = URLError("connection refused")

    with patch("app.notifications.settings") as mock_settings:
        mock_settings.slack_webhook_url = "https://hooks.slack.com/services/FAKE"

        # Should not raise an exception to the caller because notify_heal_outcome catches it
        notify_heal_outcome(make_summary())

        # tenacity should have retried exactly 3 times
        assert mock_urlopen.call_count == 3
