"""Default redaction for sensitive data persisted by the Shadow runtime."""

import json
import re
from typing import cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.shadow.schemas import (
    CapturedRequest,
    CapturedResponse,
    CookieSnapshot,
    LocalStorageSnapshot,
    NetworkSnapshot,
    ShadowSnapshot,
)

REDACTED = "[REDACTED]"
SENSITIVE_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "x-csrf-token"}
)
SENSITIVE_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
        "session",
        "cookie",
        "credential",
    }
)
_URL_RE = re.compile(r"https?://[^\s'\"<>]+")
_AUTHORIZATION_RE = re.compile(r"(?i)\b(authorization\s*:\s*(?:bearer|basic|token)\s+)[^\s,;]+")
_CREDENTIAL_PAIR_RE = re.compile(
    r"(?i)\b(token|access_token|refresh_token|api_key|apikey|password|secret|credential)\s*=\s*[^\s&,;]+"
)


def redact_value(value: object) -> object:
    """Recursively redact structured values before they are persisted or emitted."""
    if isinstance(value, dict):
        return {
            str(key): REDACTED if str(key).casefold() in SENSITIVE_KEYS else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        safe = _URL_RE.sub(lambda match: redact_url(match.group(0)), value)
        safe = _AUTHORIZATION_RE.sub(r"\1" + REDACTED, safe)
        return _CREDENTIAL_PAIR_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", safe)
    return value


def redact_url(url: str) -> str:
    """Return a URL safe to persist or log, preserving non-sensitive query values."""
    parts = urlsplit(url)
    query = [
        (key, REDACTED if key.casefold() in SENSITIVE_KEYS else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    netloc = parts.netloc.rsplit("@", 1)[-1]
    fragment = REDACTED if parts.fragment else ""
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), fragment))


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        name: REDACTED if name.casefold() in SENSITIVE_HEADERS else cast(str, redact_value(value))
        for name, value in headers.items()
    }


def _redact_json_body(body: str | None) -> str | None:
    if body is None:
        return None
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return cast(str, redact_value(body))

    return json.dumps(redact_value(value), sort_keys=True, separators=(",", ":"))


def _redact_request(request: CapturedRequest) -> CapturedRequest:
    return CapturedRequest(
        method=request.method,
        url=redact_url(request.url),
        headers=_redact_headers(request.headers),
        body=_redact_json_body(request.body),
    )


def _redact_response(response: CapturedResponse) -> CapturedResponse:
    return CapturedResponse(
        status=response.status,
        headers=_redact_headers(response.headers),
        body=_redact_json_body(response.body) if not response.is_base64 else response.body,
        is_base64=response.is_base64,
    )


def redact_snapshot(snapshot: ShadowSnapshot) -> ShadowSnapshot:
    """Create a persistable ShadowSnapshot with default credential redaction."""
    state = []
    for item in snapshot.state_snapshots:
        if isinstance(item, LocalStorageSnapshot):
            state.append(
                LocalStorageSnapshot(
                    origin=item.origin,
                    items={
                        key: REDACTED if key.casefold() in SENSITIVE_KEYS else value
                        for key, value in item.items.items()
                    },
                )
            )
        elif isinstance(item, CookieSnapshot):
            state.append(item.model_copy(update={"value": REDACTED}))
        else:
            state.append(item)
    return ShadowSnapshot(
        snapshot_id=snapshot.snapshot_id,
        metadata=snapshot.metadata,
        network_snapshots=[
            NetworkSnapshot(
                request=_redact_request(item.request),
                response=_redact_response(item.response),
                sequence=item.sequence,
                started_at=item.started_at,
                duration_ms=item.duration_ms,
            )
            for item in snapshot.network_snapshots
        ],
        state_snapshots=state,
    )
