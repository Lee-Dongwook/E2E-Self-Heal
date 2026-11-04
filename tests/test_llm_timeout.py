"""A provider that never answers ends a call with an error instead of hanging it (#471).

Each test points a real client at a local server that accepts the connection, reads the
request and never replies, with a short ``llm_timeout_seconds``. The call runs in a thread with
a hard deadline, so a regression fails the test instead of hanging the suite.
"""

import socket
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from app import llm
from app.config import settings

TIMEOUT_SECONDS = 0.3
# Generous: the SDKs retry a timed-out request twice with backoff before raising.
DEADLINE_SECONDS = 20.0


@pytest.fixture
def silent_server() -> Iterator[str]:
    """``http://127.0.0.1:<port>``: accepts every connection and never sends a byte back."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept_forever() -> None:
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            held.append(connection)

    thread = threading.Thread(target=accept_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        stop.set()
        thread.join(1)
        for connection in held:
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


def _configure(monkeypatch: pytest.MonkeyPatch, base_url: str) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_base_url", base_url)
    monkeypatch.setattr(settings, "llm_timeout_seconds", TIMEOUT_SECONDS)


@pytest.mark.parametrize("provider", ["nvidia", "openai", "orcarouter", "ollama"])
def test_a_silent_provider_raises_instead_of_hanging(
    provider: llm.LLMProvider, silent_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url = silent_server if provider == "ollama" else f"{silent_server}/v1"
    _configure(monkeypatch, base_url)
    model = llm._build_chat_model(provider)

    error = _outcome_within_deadline(lambda: model.invoke("ping"))

    assert error is not None
    assert "timeout" in type(error).__name__.lower()  # APITimeoutError, or httpx's ReadTimeout


def test_a_silent_anthropic_endpoint_raises_instead_of_hanging(
    silent_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The engine exposes no Anthropic endpoint setting; ChatAnthropic reads this variable.
    monkeypatch.setenv("ANTHROPIC_API_URL", silent_server)
    _configure(monkeypatch, "")
    model = llm._build_chat_model("anthropic")

    error = _outcome_within_deadline(lambda: model.invoke("ping"))

    assert error is not None
    assert "timeout" in type(error).__name__.lower()


def test_a_silent_deepseek_endpoint_raises_instead_of_hanging(
    silent_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, f"{silent_server}/v1")
    client = llm._build_deepseek_client()

    error = _outcome_within_deadline(lambda: client.complete("system", "ping"))

    assert error is not None
    assert "timeout" in type(error).__name__.lower()
