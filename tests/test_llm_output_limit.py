"""A response cut off by the output-token limit is retried with a larger budget (#453).

Reasoning models can spend the whole output budget thinking and return nothing usable, so
retrying with the same budget fails the same way. The OpenAI-compatible tests run the real
engine call (``generate_patch`` / ``generate_diagnosis``, tenacity included) against a local
chat-completions server that answers only when the request's budget is large enough, and
records the budget of every request.
"""

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import pytest
from tenacity import RetryError, wait_none

from app import llm
from app.config import settings

_PATCH = json.dumps({"instructions": []})


@dataclass
class FakeChat:
    """Answers with ``content`` only when a request allows ``needs_tokens`` output tokens.

    Below that it returns an empty answer, flagged ``finish_reason="length"`` when
    ``signals_cut_off`` (most providers) or as a normal ``"stop"`` otherwise (#453's case).
    """

    needs_tokens: int
    content: str = _PATCH
    signals_cut_off: bool = True
    budgets: list[int] = field(default_factory=list)
    url: str = ""


@pytest.fixture
def fake_chat() -> Iterator[FakeChat]:
    chat = FakeChat(needs_tokens=0)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server's naming
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            budget = request.get("max_completion_tokens") or request.get("max_tokens")
            chat.budgets.append(budget)
            fits = budget >= chat.needs_tokens
            body = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": request["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop" if fits or not chat.signals_cut_off else "length",
                        "message": {"role": "assistant", "content": chat.content if fits else ""},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": budget, "total_tokens": 0},
            }
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:  # quiet the test output
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    chat.url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        yield chat
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def nvidia_client(fake_chat: FakeChat, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeChat]:
    monkeypatch.setattr(settings, "llm_provider", "nvidia")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_base_url", fake_chat.url)
    monkeypatch.setattr(settings, "llm_max_tokens", 1000)
    for call in (llm.generate_patch, llm.generate_diagnosis):
        monkeypatch.setattr(cast(Any, call).retry, "wait", wait_none())
    llm._get_client.cache_clear()
    try:
        yield fake_chat
    finally:
        llm._get_client.cache_clear()


def test_a_cut_off_structured_answer_is_retried_with_double_the_budget(
    nvidia_client: FakeChat,
) -> None:
    nvidia_client.needs_tokens = 2000

    patch = llm.generate_patch("system", "user")

    assert patch == llm.PatchOutput(instructions=[])
    assert nvidia_client.budgets == [1000, 2000]


def test_the_budget_grows_to_four_times_the_configured_one_and_no_further(
    nvidia_client: FakeChat,
) -> None:
    nvidia_client.needs_tokens = 10_000

    with pytest.raises(RetryError) as raised:
        llm.generate_patch("system", "user")

    assert nvidia_client.budgets == [1000, 2000, 4000]
    final = raised.value.last_attempt.exception()
    assert isinstance(final, llm.OutputLimitReached)
    assert "E2E_HEALER_LLM_MAX_TOKENS" in str(final)  # tells the user what to change


def test_a_cut_off_diagnosis_is_retried_with_a_larger_budget_even_when_not_empty(
    nvidia_client: FakeChat,
) -> None:
    # A truncated diagnosis would mislead the patch, so it counts as cut off too.
    nvidia_client.needs_tokens = 2000
    nvidia_client.content = "The button id changed from submit-btn to submit."

    assert llm.generate_diagnosis("system", "user") == nvidia_client.content
    assert nvidia_client.budgets == [1000, 2000]


def test_other_failures_keep_the_configured_budget(nvidia_client: FakeChat) -> None:
    nvidia_client.content = "not json"

    with pytest.raises(RetryError):
        llm.generate_patch("system", "user")

    assert nvidia_client.budgets == [1000, 1000, 1000]


def test_each_call_starts_again_from_the_configured_budget(nvidia_client: FakeChat) -> None:
    nvidia_client.needs_tokens = 2000
    llm.generate_patch("system", "user")

    nvidia_client.needs_tokens = 0
    llm.generate_patch("system", "user")

    assert nvidia_client.budgets == [1000, 2000, 1000]


@pytest.mark.parametrize("call", ["generate_patch", "generate_diagnosis"])
def test_an_empty_answer_without_a_cut_off_signal_is_retried_with_a_larger_budget(
    nvidia_client: FakeChat, call: str
) -> None:
    # #453's case: the provider returns nothing and reports a normal finish.
    nvidia_client.needs_tokens = 2000
    nvidia_client.signals_cut_off = False
    if call == "generate_diagnosis":
        nvidia_client.content = "The button id changed."

    result = getattr(llm, call)("system", "user")

    assert result in (llm.PatchOutput(instructions=[]), nvidia_client.content)
    assert nvidia_client.budgets == [1000, 2000]


class _Incomplete:
    status = "incomplete"
    output_text = ""

    class incomplete_details:  # noqa: N801 - mirrors the SDK attribute
        reason = "max_output_tokens"


class _Complete:
    status = "completed"
    incomplete_details = None
    output_text = "diagnosis"


def test_deepseek_reports_a_response_cut_off_by_its_output_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The original #453 symptom: DeepSeek returned an empty completion on long diagnoses.
    budgets: list[int] = []

    class Responses:
        def create(self, **request: Any) -> object:
            budgets.append(request["max_output_tokens"])
            return _Incomplete() if request["max_output_tokens"] < 2000 else _Complete()

    class FakeOpenAI:
        def __init__(self, **_kwargs: Any) -> None:
            self.responses = Responses()

    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    client = llm.DeepSeekResponsesClient(
        api_key="k",
        base_url="https://api.deepseek.com",
        model="m",
        max_output_tokens=1000,
        timeout_seconds=30,
    )

    with pytest.raises(llm.OutputLimitReached):
        client.complete("system", "user")
    assert client.complete("system", "user", max_tokens=2000) == "diagnosis"
    assert budgets == [1000, 2000]


class _EmptyCompleted:
    status = "completed"
    incomplete_details = None
    output_text = ""


@pytest.mark.parametrize("call", ["generate_diagnosis", "generate_patch"])
def test_an_empty_deepseek_answer_is_retried_with_a_larger_budget(
    monkeypatch: pytest.MonkeyPatch, call: str
) -> None:
    # #453 exactly: DeepSeek returns an empty completion with no incomplete status.
    budgets: list[int] = []

    class Answer:
        status = "completed"
        incomplete_details = None
        output_text = "diagnosis" if call == "generate_diagnosis" else _PATCH

    class Responses:
        def create(self, **request: Any) -> object:
            budgets.append(request["max_output_tokens"])
            return _EmptyCompleted() if request["max_output_tokens"] < 2000 else Answer()

    class FakeOpenAI:
        def __init__(self, **_kwargs: Any) -> None:
            self.responses = Responses()

    monkeypatch.setattr(llm, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_base_url", "")
    monkeypatch.setattr(settings, "llm_max_tokens", 1000)
    monkeypatch.setattr(cast(Any, getattr(llm, call)).retry, "wait", wait_none())
    llm._get_client.cache_clear()
    try:
        result = getattr(llm, call)("system", "user")
    finally:
        llm._get_client.cache_clear()

    assert result in ("diagnosis", llm.PatchOutput(instructions=[]))
    assert budgets == [1000, 2000]
