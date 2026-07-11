"""Pydantic schemas for the Shadow Runtime network mock capturing and replay."""

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
