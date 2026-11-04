"""A provider that never answers ends a call with an error instead of hanging it (#471).

Each test points a real client at a local server that accepts the connection, reads the
request and never replies, with a short ``llm_timeout_seconds``. The call runs in a thread with
a hard deadline, so a regression fails the test instead of hanging the suite. The server counts
connections, which shows how many attempts a call made: tenacity is the only retry layer, so
one client call is one request and one logical engine call is three.
"""

import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from tenacity import wait_none

from app import llm
from app.config import settings

TIMEOUT_SECONDS = 0.3
DEADLINE_SECONDS = 20.0


@dataclass
class SilentServer:
    url: str
    connections: list[socket.socket] = field(default_factory=list)


@pytest.fixture
def silent_server() -> Iterator[SilentServer]:
    """Accepts every connection on ``http://127.0.0.1:<port>`` and never sends a byte back."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.05)
    server = SilentServer(url=f"http://127.0.0.1:{listener.getsockname()[1]}")
    stop = threading.Event()

    def accept_forever() -> None:
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            server.connections.append(connection)

    thread = threading.Thread(target=accept_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        stop.set()
        thread.join(1)
        for connection in server.connections:
            connection.close()
        listener.close()


def _outcome_within_deadline(call: Callable[[], Any]) -> BaseException | None:
    """Run ``call``; return what it raised, or fail if it is still running at the deadline."""
    raised: list[BaseException] = []
    finished = threading.Event()

    def run() -> None:
        try:
            call()
        except BaseException as exc:  # noqa: BLE001 - the outcome is what's under test
            raised.append(exc)
        finished.set()

    threading.Thread(target=run, daemon=True).start()
    if not finished.wait(DEADLINE_SECONDS):
        pytest.fail(f"the provider call was still waiting after {DEADLINE_SECONDS}s")
    return raised[0] if raised else None


def _configure(monkeypatch: pytest.MonkeyPatch, provider: str, base_url: str) -> None:
    monkeypatch.setattr(settings, "llm_provider", provider)
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_base_url", base_url)
    monkeypatch.setattr(settings, "llm_timeout_seconds", TIMEOUT_SECONDS)


@pytest.mark.parametrize("provider", ["nvidia", "openai", "orcarouter", "ollama"])
def test_a_silent_provider_times_out_after_one_request(
    provider: llm.LLMProvider, silent_server: SilentServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url = silent_server.url if provider == "ollama" else f"{silent_server.url}/v1"
    _configure(monkeypatch, provider, base_url)
    model = llm._build_chat_model(provider)

    error = _outcome_within_deadline(lambda: model.invoke("ping"))

    assert error is not None
    assert "timeout" in type(error).__name__.lower()  # APITimeoutError, or httpx's ReadTimeout
    assert len(silent_server.connections) == 1  # no hidden SDK retries


def test_a_silent_anthropic_endpoint_times_out_after_one_request(
    silent_server: SilentServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The engine exposes no Anthropic endpoint setting; ChatAnthropic reads this variable.
    monkeypatch.setenv("ANTHROPIC_API_URL", silent_server.url)
    _configure(monkeypatch, "anthropic", "")
    model = llm._build_chat_model("anthropic")

    error = _outcome_within_deadline(lambda: model.invoke("ping"))

    assert error is not None
    assert "timeout" in type(error).__name__.lower()
    assert len(silent_server.connections) == 1


def test_a_silent_deepseek_endpoint_times_out_after_one_request(
    silent_server: SilentServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, "deepseek", f"{silent_server.url}/v1")
    client = llm._build_deepseek_client()

    error = _outcome_within_deadline(lambda: client.complete("system", "ping"))

    assert error is not None
    assert "timeout" in type(error).__name__.lower()
    assert len(silent_server.connections) == 1


def test_one_engine_call_makes_exactly_tenacitys_three_attempts(
    silent_server: SilentServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The whole retry budget for one logical call is tenacity's: 3 attempts x the timeout.
    _configure(monkeypatch, "nvidia", f"{silent_server.url}/v1")
    # tenacity attaches its Retrying object as `.retry`; skip the 4-10 s waits between attempts.
    monkeypatch.setattr(cast(Any, llm.generate_diagnosis).retry, "wait", wait_none())
    llm._get_client.cache_clear()
    try:
        error = _outcome_within_deadline(lambda: llm.generate_diagnosis("system", "ping"))
    finally:
        llm._get_client.cache_clear()

    assert error is not None
    assert len(silent_server.connections) == 3
