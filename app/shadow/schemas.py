"""Pydantic schemas for Shadow Runtime application-state capture and replay."""

import re
from ipaddress import ip_address

from typing import Annotated, Any, Literal, Self, TypeAlias
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator


class CapturedRequest(BaseModel):
    """Schema representing an intercepted outgoing request."""
    method: str
    url: str
    headers: list[tuple[str, str]] = Field(default_factory=list)
    body: str | None = None

    @field_validator("headers", mode="before")
    @classmethod
    def normalize_headers_list(cls, value: Any) -> list[tuple[str, str]]:
        if isinstance(value, dict):
            return [(k, str(v)) for k, v in value.items()]
        if isinstance(value, list):
            res = []
            for item in value:
                if isinstance(item, tuple) and len(item) == 2:
                    res.append((str(item[0]), str(item[1])))
                elif isinstance(item, dict) and "name" in item and "value" in item:
                    res.append((str(item["name"]), str(item["value"])))
            return res
        return []

    @property
    def headers_dict(self) -> dict[str, str]:
        """Backwards-compatibility helper returning a dict (last key wins)."""
        return {k: v for k, v in self.headers}


class CapturedResponse(BaseModel):
    """Schema representing the captured HTTP response to replay."""
    status: int
    headers: list[tuple[str, str]] = Field(default_factory=list)
    body: str | None = None
    is_base64: bool = False

    @field_validator("headers", mode="before")
    @classmethod
    def normalize_headers_list(cls, value: Any) -> list[tuple[str, str]]:
        if isinstance(value, dict):
            return [(k, str(v)) for k, v in value.items()]
        if isinstance(value, list):
            res = []
            for item in value:
                if isinstance(item, tuple) and len(item) == 2:
                    res.append((str(item[0]), str(item[1])))
                elif isinstance(item, dict) and "name" in item and "value" in item:
                    res.append((str(item["name"]), str(item["value"])))
            return res
        return []

    @property
    def headers_dict(self) -> dict[str, str]:
        """Backwards-compatibility helper returning a dict (last key wins)."""
        return {k: v for k, v in self.headers}


class NetworkSnapshot(BaseModel):
    """A pair of captured request and response representing a single network interaction."""

    request: CapturedRequest
    response: CapturedResponse
    sequence: int | None = None  # ordering index within the trace
    started_at: float | None = None  # request start, epoch seconds
    duration_ms: float | None = None  # request→response duration in ms


class LocalStorageSnapshot(BaseModel):
    """Captured localStorage values for one browser origin."""

    scope: Literal["local_storage"] = "local_storage"
    origin: str
    items: dict[str, str] = Field(default_factory=dict)

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        """Require an HTTP(S) origin without credentials, path, query, or fragment."""
        if value != value.strip():
            raise ValueError("origin must not contain surrounding whitespace")
        parsed = urlsplit(value)
        try:
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise ValueError("origin must contain a valid host and optional port") from exc

        if (
            parsed.scheme.lower() not in {"http", "https"}
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("origin must be an HTTP(S) origin without a path, query, or fragment")

        normalized_host = hostname.lower()
        if ":" in normalized_host:
            normalized_host = f"[{normalized_host}]"
        normalized_port = f":{port}" if port is not None else ""
        return f"{parsed.scheme.lower()}://{normalized_host}{normalized_port}"


class CookieSnapshot(BaseModel):
    """Captured browser cookie in Playwright storage-state form."""

    scope: Literal["cookie"] = "cookie"
    name: str = Field(min_length=1)
    value: str
    domain: str = Field(min_length=1)
    path: str = "/"
    expires: float = Field(default=-1.0, allow_inf_nan=False)
    http_only: bool = False
    secure: bool = False
    same_site: Literal["Strict", "Lax", "None"] = "Lax"
    partition_key: str | None = None

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        """Require a hostname or IP cookie domain without URL components."""
        if value != value.strip() or any(char in value for char in "/?#@"):
            raise ValueError("cookie domain must be a hostname or IP address")

        candidate = value[1:] if value.startswith(".") else value
        if not candidate or candidate.startswith(".") or candidate.endswith("."):
            raise ValueError("cookie domain must contain a valid host")

        ip_candidate = (
            candidate[1:-1] if candidate.startswith("[") and candidate.endswith("]") else candidate
        )
        try:
            ip_address(ip_candidate)
        except ValueError:
            labels = candidate.split(".")
            if any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or re.fullmatch(r"[A-Za-z0-9-]+", label) is None
                for label in labels
            ):
                raise ValueError("cookie domain must contain a valid host")
        return value.lower()

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        """Require the cookie path form accepted by Playwright."""
        if not value.startswith("/"):
            raise ValueError("cookie path must start with '/'")
        return value

    @field_validator("expires")
    @classmethod
    def validate_expires(cls, value: float) -> float:
        """Accept session cookies (-1) or non-negative Unix timestamps."""
        if value != -1.0 and value < 0:
            raise ValueError("cookie expires must be -1 or a non-negative Unix timestamp")
        return value

    @model_validator(mode="after")
    def validate_same_site_security(self) -> Self:
        """Reject SameSite=None cookies that browsers would discard as insecure."""
        if self.same_site == "None" and not self.secure:
            raise ValueError("cookies with same_site='None' must set secure=True")
        return self


class ClockSnapshot(BaseModel):
    """Captured fixed wall-clock state for deterministic replay."""

    scope: Literal["clock"] = "clock"
    fixed_at: AwareDatetime
    timezone_id: str | None = None

    @field_validator("timezone_id")
    @classmethod
    def validate_timezone_id(cls, value: str | None) -> str | None:
        """Validate optional IANA timezone identifiers."""
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value


StateSnapshot: TypeAlias = Annotated[
    LocalStorageSnapshot | CookieSnapshot | ClockSnapshot,
    Field(discriminator="scope"),
]


class SnapshotMetadata(BaseModel):
    """Optional typed view of ShadowSnapshot.metadata; the field itself stays a
    permissive dict, so arbitrary keys still round-trip untouched."""

    model_config = {"extra": "allow"}

    source_url: str | None = None  # page URL the trace was captured from
    captured_at: float | None = None  # capture time, epoch seconds
    event_count: int | None = None  # number of network events in the trace


class ShadowSnapshot(BaseModel):
    """Container representing a fully serialized/persisted application state for replay."""

    snapshot_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    network_snapshots: list[NetworkSnapshot] = Field(default_factory=list)
    state_snapshots: list[StateSnapshot] = Field(default_factory=list)


class ShadowRunResult(BaseModel):
    """Result of a Shadow Replay execution run."""

    is_success: bool
    matched_count: int = 0
    missed_count: int = 0
    missed_requests: list[CapturedRequest] = Field(default_factory=list)
    score: float = 0.0
