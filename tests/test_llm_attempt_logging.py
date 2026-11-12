"""Every failed model call is logged, with its HTTP status and a redacted body excerpt (#480).

tenacity retries a call up to three times, and only the last error used to reach the log,
inside the node's traceback. These run the real ``generate_diagnosis`` (tenacity included,
waits set to zero) against a local chat-completions server that answers with a scripted
sequence of HTTP statuses, and render the log with the CLI's own logging configuration.
"""

import json
import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import pytest
import structlog
from pydantic import SecretStr
from tenacity import RetryError, wait_none

from app import llm
from app.config import settings
from app.logging import configure_logging

SECRET = "sk-live-8c7b6a5f4e3d"
_OK = {
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


class Replies:
    """The HTTP status and body for each request, in order."""

    def __init__(self) -> None:
        self.queue: list[tuple[int, str]] = []


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[Replies]:
    replies = Replies()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server's naming
            self.rfile.read(int(self.headers["Content-Length"]))
            status, body = replies.queue.pop(0)
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:  # quiet the test output
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(settings, "llm_provider", "nvidia")
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_base_url", f"http://127.0.0.1:{server.server_address[1]}/v1")
    monkeypatch.setattr(cast(Any, llm.generate_diagnosis).retry, "wait", wait_none())
    monkeypatch.setenv("E2E_TEST_PROVIDER_KEY", SECRET)
    monkeypatch.setattr(settings, "sensitive_env_vars", "E2E_TEST_PROVIDER_KEY")
    monkeypatch.setattr(settings, "sensitive_values", SecretStr(""))
    saved = structlog.get_config()
    configure_logging("INFO")
    llm._get_client.cache_clear()
    try:
        yield replies
    finally:
        llm._get_client.cache_clear()
        structlog.configure(**saved)
        server.shutdown()
        server.server_close()


def _failed_attempts(rendered: str) -> list[str]:
    plain = re.sub(r"\x1b\[[0-9;]*m", "", rendered)
    return [line for line in plain.splitlines() if "llm_attempt_failed" in line]


def _error(message: str, code: str) -> str:
    return json.dumps(
        {"error": {"message": message, "type": "invalid_request_error", "code": code}}
    )


def test_each_failed_attempt_is_logged_with_its_status_and_body(
    provider: Replies, capsys: pytest.CaptureFixture[str]
) -> None:
    provider.queue = [
        (503, '{"error": {"message": "upstream overloaded", "code": "service_unavailable"}}'),
        (400, _error("model 'test-model' does not support strict schemas", "unsupported_model")),
        (200, json.dumps(_OK)),
    ]

    assert llm.generate_diagnosis("system", "user") == "The button id changed."

    failed = _failed_attempts(capsys.readouterr().err)
    assert len(failed) == 2
    assert "attempt=1" in failed[0] and "status_code=503" in failed[0]
    assert "service_unavailable" in failed[0]
    # The raw body keeps the provider's error code, which LangChain's 400 message drops.
    assert "attempt=2" in failed[1] and "status_code=400" in failed[1]
    assert "unsupported_model" in failed[1]
    assert all("call=generate_diagnosis" in line for line in failed)


def test_a_secret_in_the_body_is_redacted_even_where_the_excerpt_cuts_it(
    provider: Replies, capsys: pytest.CaptureFixture[str]
) -> None:
    # The secret straddles the excerpt's end: cutting before redacting would leak its start.
    prefix = "x" * (llm._BODY_EXCERPT_CHARS - len('{"error": {"message": "') - 5)
    body = _error(f"{prefix}{SECRET} and more text after it", "bad_key")
    provider.queue = [(401, body)] * 3

    with pytest.raises(RetryError):
        llm.generate_diagnosis("system", "user")

    failed = _failed_attempts(capsys.readouterr().err)
    assert len(failed) == 3
    assert all("status_code=401" in line for line in failed)
    assert not any(SECRET[:5] in line for line in failed)
    # Redacted first, so the cut lands inside the marker, not inside the secret.
    assert all("[REDA…" in line for line in failed)


def test_a_failure_without_an_http_response_still_logs_the_attempt(
    provider: Replies, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = json.loads(json.dumps(_OK))
    empty["choices"][0]["message"]["content"] = ""
    provider.queue = [(200, json.dumps(empty)), (200, json.dumps(_OK))]

    llm.generate_diagnosis("system", "user")

    (failed,) = _failed_attempts(capsys.readouterr().err)
    assert "error_type=EmptyResponse" in failed
    assert "status_code" not in failed
