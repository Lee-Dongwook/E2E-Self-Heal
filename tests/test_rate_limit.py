"""Rate limits on free tiers (#456): honour the provider's retry hint, and back off together.

The parsing tests use the SDK's real exceptions. The end-to-end tests run the real
``generate_diagnosis`` (tenacity included) against a local server that answers 429s, with
tenacity's and the pacer's sleeps recorded instead of slept.
"""

import json
import subprocess
import sys
import textwrap
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import httpx2
import openai
import pytest
from structlog.testing import capture_logs

from app import llm
from app.config import settings
from app.rate_limit import MAX_RETRY_WAIT_SECONDS, Pacer, rate_limit_of

_GEMINI_PER_MINUTE = json.dumps(
    [
        {
            "error": {
                "code": 429,
                "message": "Quota exceeded for metric: generate_content_free_tier_requests, "
                "limit: 15. Please retry in 34.074824224s.",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}
                        ],
                    },
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "34s"},
                ],
            }
        }
    ]
)
_GEMINI_PER_DAY = _GEMINI_PER_MINUTE.replace("PerMinute", "PerDay").replace("34", "21")
_GROQ = json.dumps(
    {
        "error": {
            "message": "Rate limit reached for model `qwen/qwen3.8-27b` on tokens per minute "
            "(TPM): Limit 6000, Used 5800, Requested 900. Please try again in 1m26.4s.",
            "type": "tokens",
            "code": "rate_limit_exceeded",
        }
    }
)


def _rate_limit_error(body: str, headers: dict[str, str] | None = None) -> openai.RateLimitError:
    request = httpx2.Request("POST", "https://provider.example/v1/chat/completions")
    response = httpx2.Response(429, headers=headers or {}, content=body.encode(), request=request)
    return openai.RateLimitError("rate limited", response=response, body=json.loads(body))


@pytest.mark.parametrize(
    ("body", "headers", "seconds"),
    [
        ('{"error": {"message": "slow down"}}', {"retry-after": "17"}, 17.0),
        ('{"error": {"message": "slow down"}}', {"retry-after-ms": "1500"}, 1.5),
        (_GEMINI_PER_MINUTE, None, 34.0),
        (_GROQ, None, 86.4),
        ('{"error": {"message": "slow down"}}', None, None),
    ],
    ids=["retry-after", "retry-after-ms", "gemini-retry-info", "groq-message", "no-hint"],
)
def test_the_providers_retry_hint_is_read(
    body: str, headers: dict[str, str] | None, seconds: float | None
) -> None:
    error = _rate_limit_error(body, headers)

    limit = rate_limit_of(error, error.response.text)

    assert limit is not None
    assert limit.retry_after == seconds
    assert not limit.exhausted


def test_only_a_429_is_a_rate_limit() -> None:
    request = httpx2.Request("POST", "https://provider.example/v1/chat/completions")
    response = httpx2.Response(503, headers={"retry-after": "5"}, request=request)
    error = openai.InternalServerError("unavailable", response=response, body=None)

    assert rate_limit_of(error, None) is None


@pytest.mark.parametrize(
    ("body", "headers", "exhausted"),
    [
        (_GEMINI_PER_DAY, None, True),
        (
            '{"error": {"message": "slow down"}}',
            {"retry-after": str(MAX_RETRY_WAIT_SECONDS)},
            False,
        ),
        ('{"error": {"message": "slow down"}}', {"retry-after": "3600"}, True),
    ],
    ids=["gemini-per-day", "wait-at-the-cap", "wait-beyond-the-cap"],
)
def test_a_quota_that_waiting_wont_clear_is_exhausted(
    body: str, headers: dict[str, str] | None, exhausted: bool
) -> None:
    error = _rate_limit_error(body, headers)

    limit = rate_limit_of(error, error.response.text)

    assert limit is not None and limit.exhausted is exhausted


def test_a_cooldown_set_by_another_process_holds_this_one_back(tmp_path: Path) -> None:
    state = tmp_path / "pacing.json"
    other = textwrap.dedent(
        f"""
        from pathlib import Path
        from app.rate_limit import Pacer
        Pacer(Path({str(state)!r}), 0).cool_down(30)
        """
    )
    subprocess.run([sys.executable, "-c", other], check=True, capture_output=True)
    slept: list[float] = []

    waited = Pacer(state, 0, sleep=slept.append).wait_turn()

    assert 25 < waited <= 30
    assert slept == [waited]


def test_a_requests_per_minute_pace_spaces_calls_across_pacers(tmp_path: Path) -> None:
    # Two pacers on one file stand in for two workers; the clock stays still.
    state = tmp_path / "pacing.json"
    first = Pacer(state, 12, clock=lambda: 1000.0, sleep=lambda _s: None)
    second = Pacer(state, 12, clock=lambda: 1000.0, sleep=lambda _s: None)

    waits = [first.wait_turn(), second.wait_turn(), first.wait_turn()]

    assert waits == [0.0, 5.0, 10.0]


class Replies:
    def __init__(self) -> None:
        self.queue: list[tuple[int, dict[str, str], str]] = []
        self.requests = 0


_OK = json.dumps(
    {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "The button id changed."},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
)


@pytest.fixture
def provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[Replies, list[float]]]:
    """A chat endpoint answering scripted replies, and every sleep the call would have taken."""
    replies = Replies()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server's naming
            self.rfile.read(int(self.headers["Content-Length"]))
            replies.requests += 1
            status, headers, body = replies.queue.pop(0)
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:  # quiet the test output
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    slept: list[float] = []
    pacer = Pacer(tmp_path / "pacing.json", 0, sleep=slept.append)
    monkeypatch.setattr(settings, "llm_provider", "nvidia")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_base_url", f"http://127.0.0.1:{server.server_address[1]}/v1")
    monkeypatch.setattr(llm, "_pacer", lambda: pacer)
    monkeypatch.setattr(cast(Any, llm.generate_diagnosis).retry, "sleep", slept.append)
    llm._get_client.cache_clear()
    try:
        yield replies, slept
    finally:
        llm._get_client.cache_clear()
        server.shutdown()
        server.server_close()


def test_a_429_is_retried_after_exactly_the_wait_the_provider_asked_for(
    provider: tuple[Replies, list[float]],
) -> None:
    replies, slept = provider
    replies.queue = [(429, {}, _GEMINI_PER_MINUTE), (200, {}, _OK)]

    assert llm.generate_diagnosis("system", "user") == "The button id changed."

    # tenacity adds no backoff (0.0); the shared cooldown holds the retry back for 34 s.
    assert slept[0] == 0.0
    assert 33 < slept[1] <= 34
    assert len(slept) == 2


def test_a_spent_daily_quota_is_not_retried(provider: tuple[Replies, list[float]]) -> None:
    replies, _slept = provider
    replies.queue = [(429, {}, _GEMINI_PER_DAY)] * 3

    # Not retried, so tenacity raises the provider's own error (every node catches Exception).
    with capture_logs() as logs, pytest.raises(openai.RateLimitError):
        llm.generate_diagnosis("system", "user")

    assert replies.requests == 1
    (exhausted,) = [log for log in logs if log["event"] == "llm_quota_exhausted"]
    assert exhausted["daily"] is True
    assert "E2E_HEALER_LLM_REQUESTS_PER_MINUTE" in exhausted["guidance"]


def test_other_failures_still_back_off_with_jitter(provider: tuple[Replies, list[float]]) -> None:
    replies, slept = provider
    replies.queue = [(500, {}, '{"error": {"message": "boom"}}'), (200, {}, _OK)]

    llm.generate_diagnosis("system", "user")

    assert len(slept) == 1
    assert 4 <= slept[0] <= 6
