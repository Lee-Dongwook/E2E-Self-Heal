"""structlog configuration for the whole application."""

import logging
import sys
from typing import Any, MutableMapping, TextIO, cast

import structlog

from app.shadow.redaction import redact_text, redact_value


class _CurrentStderr:
    """Write to whatever ``sys.stderr`` is at write time, not when logging was configured.

    Loggers are cached on first use, so a stream bound at configure time outlives any later
    replacement of ``sys.stderr`` (a test runner's capture, for example) and can be closed.
    """

    def write(self, text: str) -> int:
        return sys.stderr.write(text)

    def flush(self) -> None:
        sys.stderr.flush()


def _loggable(value: object) -> object:
    """``value`` as plain data the redaction can see into (#460).

    ``redact_value`` walks dicts, lists and strings. Anything else used to reach the renderer
    untouched and be printed by its ``repr``: a tuple or set of strings, an exception passed as
    a value, a pydantic model, a ``Path`` or ``bytes`` printed a configured secret as is. Those
    become lists or their text here, so every leaf that gets rendered is redacted.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {key: _loggable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_loggable(item) for item in value]
    if isinstance(value, bytes | bytearray):
        return bytes(value).decode("utf-8", errors="replace")
    return repr(value)


def redact_event(
    _logger: object, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Redact every rendered log event, including tracebacks and exception messages.

    A structlog processor rather than a per-call-site rule, so a message built from a Playwright
    log or a provider error cannot leak a configured secret just because that one caller forgot.
    Runs after ``format_exc_info`` so a traceback's text is covered too.
    """
    return cast(MutableMapping[str, Any], redact_value(_loggable(dict(event_dict))))


class _RedactingFormatter(logging.Formatter):
    """Redact stdlib log records: what third-party libraries log never passes through structlog.

    httpx logs every request URL at INFO and the OpenAI SDK logs request options at DEBUG, so a
    key in a base URL or a header reached stderr as is (#460).
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog once at process start.

    Logs go to stderr, never stdout: ``--json`` writes the machine-readable result to stdout
    and CI redirects it to a file (``e2e-healer ... --json > repair-summary.json``), so any
    log line there would make that file invalid JSON.
    """
    handler = logging.StreamHandler(cast(TextIO, _CurrentStderr()))
    handler.setFormatter(_RedactingFormatter("%(message)s"))
    # Like basicConfig always did, this is a no-op when the root logger already has handlers.
    logging.basicConfig(level=level, handlers=[handler])
    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=cast(TextIO, _CurrentStderr())),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_event,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        cache_logger_on_first_use=True,
    )
