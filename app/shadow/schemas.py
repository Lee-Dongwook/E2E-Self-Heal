"""Pydantic schemas for the Shadow Runtime network mock capturing and replay."""

from typing import Any

from pydantic import BaseModel, Field


class CapturedRequest(BaseModel):
    """Schema representing an intercepted outgoing request."""

    method: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None


class CapturedResponse(BaseModel):
    """Schema representing the captured HTTP response to replay."""

    status: int
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    is_base64: bool = False


class NetworkSnapshot(BaseModel):
    """A pair of captured request and response representing a single network interaction."""

    request: CapturedRequest
    response: CapturedResponse
    sequence: int | None = None  # ordering index within the trace
    started_at: float | None = None  # request start, epoch seconds
    duration_ms: float | None = None  # request→response duration in ms


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
