"""Redaction at every exit, and the bypasses an audit found (#460).

Redaction has been fixed one channel at a time (#377, #402, #413, #480). Each test here sends a
secret out through one channel and fails if any of it comes back unredacted: a secret named in
``E2E_HEALER_SENSITIVE_ENV_VARS`` or ``E2E_HEALER_SENSITIVE_VALUES``, or an unconfigured one that
only a built-in pattern can catch. Channels already covered elsewhere (each node's prompt in
``test_evidence_boundary``, the ``--json`` bundle in ``test_evidence``, tracebacks in
``test_logging``, worker logs in ``test_safety_parallel``) are not repeated.
"""

import json
import logging
import random
import re
import string
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO, cast

import pytest
import structlog
from pydantic import BaseModel, SecretStr

import app.logging as app_logging
from app import notifications
from app.config import settings
from app.healing_history import append_record, find_match, history_path, make_record
from app.logging import configure_logging
from app.preprocess.error_log_parser import parse_error_log
from app.schemas import PatchInstruction, RepairSummary
from app.shadow.redaction import REDACTED, RedactionRecord, redact_text

SECRET = "sk-live-7f6e5d4c3b2a"
UNCONFIGURED = "private-unconfigured-91"


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name SECRET as a user would, through an environment variable."""
    monkeypatch.setenv("E2E_TEST_PROVIDER_KEY", SECRET)
    monkeypatch.setattr(settings, "sensitive_env_vars", "E2E_TEST_PROVIDER_KEY")
    monkeypatch.setattr(settings, "sensitive_values", SecretStr(""))


@pytest.fixture
def cli_logging(configured: None) -> Iterator[None]:
    """The CLI's logging, with the root logger as bare as at process start.

    pytest installs its own handlers on the root logger, and ``configure_logging`` (like
    ``basicConfig``) leaves a root that already has handlers alone; remove them for the test.
    """
    saved_config = structlog.get_config()
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers = []
    configure_logging("DEBUG")
    try:
        yield
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        structlog.configure(**saved_config)


def _stderr(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    assert SECRET not in captured.out and UNCONFIGURED not in captured.out
    return re.sub(r"\x1b\[[0-9;]*m", "", captured.err)


class _Model(BaseModel):
    key: str


class _Opaque:
    def __repr__(self) -> str:
        return f"<Opaque holding {SECRET}>"


# --- Structured logs: every value type the renderer can print ------------------------------


@pytest.mark.parametrize(
    "value",
    [
        ("argv", SECRET),
        {SECRET},
        frozenset({SECRET}),
        RuntimeError(f"request failed for {SECRET}"),
        _Model(key=SECRET),
        Path(f"/tmp/{SECRET}/cache"),
        SECRET.encode(),
        {"nested": ("a", [SECRET])},
        _Opaque(),
    ],
    ids=["tuple", "set", "frozenset", "exception", "pydantic", "path", "bytes", "nested", "repr"],
)
def test_a_configured_secret_in_any_log_value_type_is_redacted(
    value: object, cli_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # Only dicts, lists and strings were redacted; other values were printed by repr as is.
    structlog.get_logger("coverage").warning("value_probe", value=value)

    rendered = _stderr(capsys)
    assert "value_probe" in rendered
    assert SECRET not in rendered
    assert REDACTED in rendered


def test_an_unconfigured_secret_in_a_serialized_structure_is_redacted(
    cli_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # A body or a dict serialized before logging is one string: redaction by key never saw it.
    structlog.get_logger("coverage").warning(
        "body_probe",
        body=json.dumps({"error": "denied", "api_key": UNCONFIGURED}),
        options=repr({"headers": {"Authorization": f"Bearer {UNCONFIGURED}"}}),
    )

    rendered = _stderr(capsys)
    assert UNCONFIGURED not in rendered
    assert rendered.count(REDACTED) == 2
    assert "denied" in rendered


# --- Third-party libraries log through stdlib logging, not structlog -------------------------


def test_a_library_log_record_is_redacted(
    cli_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # What httpx and the OpenAI SDK actually log, at INFO and DEBUG.
    logging.getLogger("httpx").info(
        'HTTP Request: POST %s "HTTP/1.1 200 OK"',
        f"https://proxy.example/v1?api_key={UNCONFIGURED}",
    )
    logging.getLogger("openai._base_client").debug(
        "Request options: %s", {"headers": {"Authorization": f"Bearer {SECRET}"}}
    )

    rendered = _stderr(capsys)
    assert "HTTP Request: POST https://proxy.example/v1" in rendered
    assert "Request options" in rendered
    assert UNCONFIGURED not in rendered
    assert SECRET not in rendered


def test_a_library_exception_traceback_is_redacted(
    cli_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        raise ConnectionError(f"proxy refused token={UNCONFIGURED} for key {SECRET}")
    except ConnectionError:
        logging.getLogger("httpcore").exception("connection failed")

    rendered = _stderr(capsys)
    assert "ConnectionError" in rendered
    assert UNCONFIGURED not in rendered and SECRET not in rendered


# --- Free text: quoted sensitive keys --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"api_key": "abc123"}', f'{{"api_key": "{REDACTED}"}}'),
        ("{'Authorization': 'Bearer abc.def'}", f"{{'Authorization': '{REDACTED}'}}"),
        ('{"apiKey":"abc"}', f'{{"apiKey":"{REDACTED}"}}'),
        ('{"password": "p\\"q"}', f'{{"password": "{REDACTED}"}}'),
        ('{"X-Api-Key" : "abc"}', f'{{"X-Api-Key" : "{REDACTED}"}}'),
    ],
    ids=["json", "repr-header", "camel-case", "escaped-quote", "header-name"],
)
def test_a_quoted_sensitive_value_is_redacted(text: str, expected: str) -> None:
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        '{"password_hint": "your pet"}',
        '- textbox "Password: at least 8 characters"',
        "Password: at least 8 characters",
        '{"token": ""}',
        '{"name": "api_key"}',
    ],
    ids=["other-key", "aria-label", "ui-text", "empty", "key-as-value"],
)
def test_ordinary_text_that_mentions_a_sensitive_word_is_kept(text: str) -> None:
    assert redact_text(text) == text


def test_redacted_json_is_still_json_and_counts_each_value_once() -> None:
    record = RedactionRecord()
    body = json.dumps({"api_key": "a1", "user": {"password": "p1", "secret": REDACTED}})

    safe = redact_text(body, record=record)

    assert json.loads(safe) == {
        "api_key": REDACTED,
        "user": {"password": REDACTED, "secret": REDACTED},
    }
    assert record.count == 2


# --- Outputs that leave the machine or are persisted -----------------------------------------


def test_the_slack_notification_carries_no_secret(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A patched line is test source, which may hard-code a credential.
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(settings, "slack_webhook_url", "https://hooks.slack.example/T/B/X")
    monkeypatch.setattr(notifications, "_post_to_slack", lambda payload: sent.append(payload))
    line = f'await page.fill("#key", "{SECRET}"); await page.click("#old-submit");'
    summary = RepairSummary(
        test_script_path="tests/login.spec.ts",
        is_success=True,
        loop_count=1,
        instructions=[
            PatchInstruction(
                line=4,
                original=line,
                replacement=line.replace("#old-submit", "#submit"),
                reason=f"renamed (api_key={UNCONFIGURED})",
                selector="#submit",
            )
        ],
    )

    notifications.notify_heal_outcome(summary)

    (payload,) = sent
    text = json.dumps(payload)
    assert SECRET not in text and UNCONFIGURED not in text
    assert "#submit" in text


def test_the_healing_history_file_stores_no_secret_and_still_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The signature is the parsed error's first line, lowercased, with quoted values and digits
    # masked; so these secrets are digit-free and unquoted, on the `Error:` line itself. Once
    # lowercased, a configured secret could not even be matched in the file: it must be
    # redacted first.
    configured_secret, unconfigured = "sk-live-KeepOutOfHistory", "private-token-value"
    monkeypatch.setenv("E2E_TEST_PROVIDER_KEY", configured_secret)
    monkeypatch.setattr(settings, "sensitive_env_vars", "E2E_TEST_PROVIDER_KEY")
    monkeypatch.setattr(settings, "sensitive_values", SecretStr(""))
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    error_log = parse_error_log(
        f"Error: page.click: proxy rejected token={unconfigured} for key {configured_secret}\n"
        "Call log:\n"
        "  - waiting for locator('#old')\n"
    )
    assert configured_secret in error_log and unconfigured in error_log
    test_path = str(tmp_path / "tests" / "login.spec.ts")
    record = make_record(
        error_log=error_log,
        instructions=[
            PatchInstruction(
                line=1,
                original="await page.click('#old')",
                replacement="await page.click('#new')",
                reason="renamed",
                selector="#new",
            )
        ],
        test_script_path=test_path,
        source="llm",
    )
    assert record is not None
    assert append_record(record) is True

    stored = history_path().read_text().lower()
    assert configured_secret.lower() not in stored
    assert unconfigured not in stored
    match, score = find_match(error_log, test_path)
    assert match is not None and score >= 0.95


# --- Property: a configured secret never survives, whatever surrounds it ----------------------

# Secrets from lowercase, digits and punctuation; filler from uppercase and spaces. Neither can
# occur by chance inside the other, or inside the "[REDACTED]" marker.
_SECRET_CHARS = string.ascii_lowercase + string.digits + "-_.:/+=@!~"
_FILLER_CHARS = string.ascii_uppercase + "    "


def _cases(count: int) -> Iterator[tuple[str, str]]:
    rng = random.Random(460)  # fixed seed: the same cases on every run
    for _ in range(count):
        secret = "".join(rng.choices(_SECRET_CHARS, k=rng.randint(4, 40)))
        pieces = ["".join(rng.choices(_FILLER_CHARS, k=rng.randint(0, 30))) for _ in range(4)]
        text = secret.join(pieces[: rng.randint(2, 4)])
        yield secret, text


def test_a_configured_secret_never_survives_redact_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sensitive_env_vars", "")
    for secret, text in _cases(500):
        monkeypatch.setattr(settings, "sensitive_values", SecretStr(secret))
        assert secret not in redact_text(text), (secret, text)


def test_a_configured_secret_never_survives_the_log_pipeline(
    cli_logging: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log = structlog.get_logger("coverage")
    rng = random.Random(4600)
    wrappers = [
        lambda text: text,
        lambda text: (text,),
        lambda text: {"k": [text]},
        lambda text: RuntimeError(text),
        lambda text: text.encode(),
    ]
    for secret, text in _cases(200):
        monkeypatch.setenv("E2E_TEST_PROVIDER_KEY", secret)
        log.warning("property_probe", value=rng.choice(wrappers)(text))
        rendered = capsys.readouterr().err
        assert secret not in rendered, (secret, text)


# --- Fail closed: redaction that cannot complete prints nothing unredacted -------------------

_MALFORMED_URL = "http://[ "  # urlsplit raises ValueError("Invalid IPv6 URL")


def test_a_url_that_cannot_be_parsed_is_replaced_whole(configured: None) -> None:
    safe = redact_text(f"proxy at {_MALFORMED_URL}{SECRET} refused")

    assert SECRET not in safe
    assert safe.startswith("proxy at ")


def test_a_library_record_with_a_malformed_url_prints_no_secret(
    cli_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    # Before, the formatter raised, and logging's error report printed the message and its
    # arguments to stderr as they were.
    logging.getLogger("httpx").warning("bad URL %s%s", _MALFORMED_URL, SECRET)

    rendered = _stderr(capsys)
    assert SECRET not in rendered
    assert "Arguments:" not in rendered
    assert "bad URL" in rendered


def test_a_log_value_with_a_malformed_url_neither_raises_nor_leaks(
    cli_logging: None, capsys: pytest.CaptureFixture[str]
) -> None:
    structlog.get_logger("coverage").warning("url_probe", url=f"{_MALFORMED_URL}{SECRET}")

    rendered = _stderr(capsys)
    assert "url_probe" in rendered
    assert SECRET not in rendered


def _broken_redaction(*_args: object, **_kwargs: object) -> object:
    raise RuntimeError("redaction bug")


def test_if_redaction_fails_a_library_record_is_withheld(
    cli_logging: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(app_logging, "redact_text", _broken_redaction)

    logging.getLogger("httpx").warning("request with key %s", SECRET)

    rendered = _stderr(capsys)
    assert SECRET not in rendered
    assert "httpx: [log record withheld, redaction failed: RuntimeError]" in rendered


def test_if_redaction_fails_a_structured_event_keeps_only_its_name(
    cli_logging: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(app_logging, "redact_value", _broken_redaction)

    structlog.get_logger("coverage").warning("provider_failed", error=f"key {SECRET}")
    structlog.get_logger("coverage").warning(f"free text {SECRET}")

    rendered = _stderr(capsys)
    assert SECRET not in rendered
    assert "provider_failed" in rendered and "[event withheld]" in rendered
    assert "redaction_failed=RuntimeError" in rendered


def test_a_handler_error_report_withholds_the_record(capsys: pytest.CaptureFixture[str]) -> None:
    # Any failure while emitting (here a broken stream) goes to handleError, whose default
    # prints the record's message and arguments.
    class BrokenStream:
        def write(self, _text: str) -> int:
            raise OSError("stream closed")

        def flush(self) -> None:
            pass

    handler = app_logging._RedactingStreamHandler(cast(TextIO, BrokenStream()))
    handler.setFormatter(app_logging._RedactingFormatter("%(message)s"))
    record = logging.LogRecord("httpx", logging.WARNING, __file__, 1, "key %s", (SECRET,), None)

    handler.handle(record)

    rendered = capsys.readouterr().err
    assert SECRET not in rendered
    assert "logging error in httpx (record withheld" in rendered
