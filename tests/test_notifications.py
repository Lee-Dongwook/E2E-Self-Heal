"""Tests for the Slack notifier (Issue #124)."""

import json
from email.message import Message
from typing import Any, cast
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest
from tenacity import wait_none

from app.notifications import _is_transient_error, _post_to_slack, notify_heal_outcome
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


def make_http_error(status: int) -> HTTPError:
    return HTTPError(
        url="https://hooks.slack.com/services/FAKE",
        code=status,
        msg="error",
        hdrs=Message(),
        fp=None,
    )


def test_noop_when_webhook_unset() -> None:
    """Should do nothing if slack_webhook_url is empty."""
    with patch("app.notifications.settings") as mock_settings:
        mock_settings.slack_webhook_url = ""
        with patch("app.notifications.urllib.request.urlopen") as mock_urlopen:
            notify_heal_outcome(make_summary())
            mock_urlopen.assert_not_called()


@pytest.mark.parametrize("is_success", [True, False], ids=["healed", "failed"])
@patch("app.notifications.urllib.request.urlopen")
def test_posts_structured_payload_when_configured(
    mock_urlopen: MagicMock, is_success: bool
) -> None:
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
    summary = make_summary(is_success=is_success, instructions=[instr] if is_success else [])

    with patch("app.notifications.settings") as mock_settings:
        mock_settings.slack_webhook_url = "https://hooks.slack.com/services/FAKE"
        notify_heal_outcome(summary)

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://hooks.slack.com/services/FAKE"

        payload = json.loads(req.data.decode("utf-8"))
        assert payload["blocks"][0]["type"] == "header"
        assert summary.test_script_path in payload["text"]
        assert summary.test_script_path in payload["blocks"][1]["fields"][0]["text"]
        if is_success:
            assert "button id changed" in payload["text"]


@patch("app.notifications.urllib.request.urlopen")
def test_retry_on_transient_error(mock_urlopen: MagicMock) -> None:
    """Should retry on URLError and eventually log failure without crashing."""
    mock_urlopen.side_effect = URLError("connection refused")
    retry_config = cast(Any, _post_to_slack).retry
    original_wait = retry_config.wait
    retry_config.wait = wait_none()

    try:
        with patch("app.notifications.settings") as mock_settings:
            mock_settings.slack_webhook_url = "https://hooks.slack.com/services/FAKE"

            # Should not raise an exception to the caller because notify_heal_outcome catches it
            notify_heal_outcome(make_summary())

            # tenacity should have retried exactly 3 times
            assert mock_urlopen.call_count == 3
    finally:
        retry_config.wait = original_wait


@pytest.mark.parametrize("status", [429, 500, 503, 599])
def test_http_status_retry_policy_treats_429_and_5xx_as_transient(status: int) -> None:
    """Should retry only rate limits and server errors."""
    assert _is_transient_error(make_http_error(status))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 431, 451])
def test_http_status_retry_policy_treats_other_4xx_as_permanent(status: int) -> None:
    """Should not retry permanent client errors."""
    assert not _is_transient_error(make_http_error(status))


@pytest.mark.parametrize(
    ("status", "expected_attempts"),
    [
        (429, 3),
        (500, 3),
        (599, 3),
        (431, 1),
        (451, 1),
    ],
)
@patch("app.notifications.urllib.request.urlopen")
def test_http_retry_attempt_count_matches_status_policy(
    mock_urlopen: MagicMock, status: int, expected_attempts: int
) -> None:
    """Should retry configured transient HTTP statuses and attempt permanent 4xx once."""
    mock_urlopen.side_effect = make_http_error(status)
    retry_config = cast(Any, _post_to_slack).retry
    original_wait = retry_config.wait
    retry_config.wait = wait_none()

    try:
        with patch("app.notifications.settings") as mock_settings:
            mock_settings.slack_webhook_url = "https://hooks.slack.com/services/FAKE"

            notify_heal_outcome(make_summary())

            assert mock_urlopen.call_count == expected_attempts
    finally:
        retry_config.wait = original_wait
