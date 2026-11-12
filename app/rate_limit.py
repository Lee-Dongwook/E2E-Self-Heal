"""Provider rate limits: read a 429's retry hint, and pace calls across processes (#456).

Free tiers allow a few requests per minute. A 429 used to get the same short, fixed backoff
as any other failure, so every ``--jobs`` worker kept hitting the limit on its own, and a
spent daily quota was retried as if it would clear in seconds. Two pieces fix that:

* :func:`rate_limit_of` reads how long the provider asked to wait (``Retry-After``, OpenAI's
  ``retry-after-ms``, Gemini's ``RetryInfo.retryDelay``, or "retry in 34s" in the message)
  and whether a per-day quota ran out.
* :class:`Pacer` keeps one cooldown, and optionally a requests-per-minute pace, in a file
  shared by every process calling the same endpoint with the same key. A 429 in one worker
  pauses all of them until the provider's retry time, so the pool backs off together.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl module.
    fcntl = None

# A hint longer than this is not a per-minute limit clearing: waiting it out would stall the
# run for minutes, so the call stops and says the quota is used up.
MAX_RETRY_WAIT_SECONDS = 90.0
# A 429 without a hint still pauses every worker for this long.
DEFAULT_COOLDOWN_SECONDS = 10.0

_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')
_RETRY_IN = re.compile(
    r"(?:retry|try again) in\s+((?:\d+h)?(?:\d+m(?!s))?(?:\d+(?:\.\d+)?s)?)", re.IGNORECASE
)
_DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?")
# Gemini's QuotaFailure names the quota (GenerateRequestsPerDayPerProjectPerModel-FreeTier);
# Groq's message says "requests per day (RPD)".
_PER_DAY = re.compile(r"PerDay|per day", re.IGNORECASE)


@dataclass(frozen=True)
class RateLimit:
    """What a 429 said: how long to wait, if it said, and whether a daily quota ran out."""

    retry_after: float | None
    daily: bool

    @property
    def exhausted(self) -> bool:
        """Waiting won't help within a run: a daily quota, or a wait longer than the cap."""
        return self.daily or (
            self.retry_after is not None and self.retry_after > MAX_RETRY_WAIT_SECONDS
        )


def _duration(text: str) -> float | None:
    match = _DURATION.fullmatch(text)
    if match is None or not any(match.groups()):
        return None
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def _header_delay(headers: Mapping[str, str]) -> float | None:
    millis = headers.get("retry-after-ms")
    if millis is not None:
        try:
            return float(millis) / 1000
        except ValueError:
            pass
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:  # Retry-After may also be an HTTP date.
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def rate_limit_of(exc: BaseException, body: str | None) -> RateLimit | None:
    """Read a provider's 429, or return None if ``exc`` isn't one.

    ``body`` is the raw response body (see ``app.llm._response_body``); the headers come from
    the HTTP response the SDK exception carries, when it carries one.
    """
    if getattr(exc, "status_code", None) != 429:
        return None
    headers = getattr(getattr(exc, "response", None), "headers", None)
    delay = _header_delay(headers) if isinstance(headers, Mapping) else None
    text = body or ""
    if delay is None and (found := _RETRY_DELAY.search(text)):
        delay = float(found.group(1))
    if delay is None and (found := _RETRY_IN.search(text)):
        delay = _duration(found.group(1))
    return RateLimit(retry_after=delay, daily=bool(_PER_DAY.search(text)))


def pacing_path(provider: str, endpoint: str, api_key: str) -> Path:
    """The state file shared by every process calling ``endpoint`` with ``api_key``.

    Quotas belong to a key (Gemini: its project) on an endpoint, so that is what is shared.
    Only a hash of the key is used, and only in the file name.
    """
    digest = hashlib.sha256(f"{provider}\n{endpoint}\n{api_key}".encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"e2e-healer-pacing-{digest}.json"


class Pacer:
    """One cooldown, and optionally a requests-per-minute pace, for a key and endpoint.

    The state lives in a file under an OS lock, so every process sharing the key (each
    ``--jobs`` worker, or two runs on one machine) sees the same cooldown and draws from the
    same pace. Without ``fcntl`` (Windows) the state is per process.
    """

    def __init__(
        self,
        path: Path,
        requests_per_minute: int,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._path = path
        self._interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._local: dict[str, float] = {}

    @contextmanager
    def _state(self) -> Iterator[dict[str, float]]:
        if fcntl is None:
            yield self._local
            return
        descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            raw = os.read(descriptor, 4096)
            try:
                loaded: Any = json.loads(raw) if raw else {}
            except ValueError:
                loaded = {}
            state: dict[str, float] = {
                key: float(value)
                for key, value in (loaded.items() if isinstance(loaded, dict) else ())
                if isinstance(value, int | float)
            }
            yield state
            encoded = json.dumps(state).encode()
            os.ftruncate(descriptor, 0)
            os.pwrite(descriptor, encoded, 0)
        finally:
            os.close(descriptor)  # closing the descriptor releases the lock

    def wait_turn(self) -> float:
        """Sleep until this call may start; return how long that was.

        A call starts after any cooldown and, with a pace set, in its own slot: the slot is
        reserved under the lock and the sleep happens outside it, so waiting never blocks
        another process from reserving the next slot.
        """
        now = self._clock()
        with self._state() as state:
            start = max(now, state.get("cooldown_until", 0.0))
            if self._interval:
                start = max(start, state.get("next_slot", 0.0))
                state["next_slot"] = start + self._interval
        delay = start - now
        if delay > 0:
            self._sleep(delay)
        return max(0.0, delay)

    def cool_down(self, seconds: float) -> None:
        """Hold every call back for ``seconds`` from now (never shortening a longer pause)."""
        until = self._clock() + min(seconds, MAX_RETRY_WAIT_SECONDS)
        with self._state() as state:
            state["cooldown_until"] = max(state.get("cooldown_until", 0.0), until)
        logger.info(
            "llm_rate_limit_cooldown", seconds=round(min(seconds, MAX_RETRY_WAIT_SECONDS), 1)
        )
