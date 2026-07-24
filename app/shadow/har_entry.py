"""Shared normalization helpers for HAR-backed trace parsers."""

from collections.abc import Callable
from datetime import datetime
from email.message import Message
from urllib.parse import urlencode

from app.shadow.schemas import CapturedRequest, CapturedResponse

BodyResolver = Callable[[object], tuple[str | None, bool]]
_DEFAULT_MULTIPART_BOUNDARY = "----e2e-self-heal-har-boundary"


def headers_to_dict(headers: object) -> dict[str, str]:
    """Convert HAR header lists into the normalized dictionary representation."""
    result: dict[str, str] = {}
    if not isinstance(headers, list):
        return result
    for header in headers:
        if not isinstance(header, dict):
            continue
        name = header.get("name")
        if not isinstance(name, str) or not name:
            continue
        value = header.get("value")
        result[name] = value if isinstance(value, str) else ""
    return result


def request_from_har(request: object) -> CapturedRequest | None:
    """Map a HAR request object onto the normalized request schema."""
    if not isinstance(request, dict):
        return None
    method = request.get("method")
    url = request.get("url")
    if not isinstance(method, str) or not isinstance(url, str) or not method or not url:
        return None

    body = request_body_from_har(request.get("postData"))
    return CapturedRequest(
        method=method,
        url=url,
        headers=headers_to_dict(request.get("headers")),
        body=body,
    )


def request_body_from_har(post_data: object) -> str | None:
    """Resolve HAR ``postData`` text or reconstruct supported parameter bodies."""
    if not isinstance(post_data, dict):
        return None

    text = post_data.get("text")
    if isinstance(text, str):
        return text

    mime_type = post_data.get("mimeType")
    params = post_data.get("params")
    if not isinstance(mime_type, str) or not isinstance(params, list):
        return None

    if mime_type.lower().startswith("application/x-www-form-urlencoded"):
        fields = _form_fields(params)
        return urlencode(fields) if fields else None
    if mime_type.lower().startswith("multipart/form-data"):
        return _multipart_body(params, mime_type)
    return None


def _form_fields(params: list[object]) -> list[tuple[str, str]]:
    """Return valid HAR parameter name/value pairs in source order."""
    fields: list[tuple[str, str]] = []
    for param in params:
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        if not isinstance(name, str):
            continue
        value = param.get("value")
        fields.append((name, value if isinstance(value, str) else ""))
    return fields


def _multipart_body(params: list[object], mime_type: str) -> str | None:
    """Reconstruct a deterministic multipart body from HAR parameters."""
    message = Message()
    message["content-type"] = mime_type
    boundary = message.get_param("boundary") or _DEFAULT_MULTIPART_BOUNDARY

    parts: list[str] = []
    for param in params:
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        if not isinstance(name, str):
            continue

        disposition = f'Content-Disposition: form-data; name="{_quote_header_value(name)}"'
        file_name = param.get("fileName")
        if isinstance(file_name, str):
            disposition += f'; filename="{_quote_header_value(file_name)}"'

        headers = [disposition]
        content_type = param.get("contentType")
        if isinstance(content_type, str):
            headers.append(f"Content-Type: {content_type}")
        value = param.get("value")
        body = value if isinstance(value, str) else ""
        parts.append(f"--{boundary}\r\n" + "\r\n".join(headers) + f"\r\n\r\n{body}\r\n")

    if not parts:
        return None
    return "".join(parts) + f"--{boundary}--\r\n"


def _quote_header_value(value: str) -> str:
    """Escape a string used in a quoted multipart header parameter."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def response_from_har(
    response: object,
    resolve_body: BodyResolver,
) -> CapturedResponse | None:
    """Map a HAR response object using the supplied content-body resolver."""
    if not isinstance(response, dict):
        return None
    status = response.get("status")
    if isinstance(status, bool) or not isinstance(status, int) or status <= 0:
        return None

    body, is_base64 = resolve_body(response.get("content"))
    return CapturedResponse(
        status=status,
        headers=headers_to_dict(response.get("headers")),
        body=body,
        is_base64=is_base64,
    )


def inline_body_from_har(content: object) -> tuple[str | None, bool]:
    """Resolve a standard HAR inline response body and its base64 marker."""
    if not isinstance(content, dict):
        return None, False
    text = content.get("text")
    if not isinstance(text, str):
        return None, False
    return text, content.get("encoding") == "base64"


def started_at_from_har(entry: dict) -> float | None:
    """Parse HAR ``startedDateTime`` into epoch seconds."""
    started = entry.get("startedDateTime")
    if not isinstance(started, str) or not started:
        return None
    try:
        parsed = datetime.fromisoformat(started.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except ValueError:
        return None


def duration_ms_from_har(entry: dict) -> float | None:
    """Return the HAR total duration in milliseconds when numeric."""
    value = entry.get("time")
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None
