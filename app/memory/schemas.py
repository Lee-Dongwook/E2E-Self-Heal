"""Pydantic models for the healing-history memory subsystem."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class HealingRecord(BaseModel):
    """A single successfully healed failure, stored for future similarity lookup.

    The record is intentionally compact: it stores the minimal context needed for
    heuristic matching (error signature, broken selector shape) plus the proven
    patch that fixed it.
    """

    id: str = Field(default_factory=lambda: _utc_timestamp_id())
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    test_script_path: str
    framework: str = Field(default="", description="Detected frontend framework (react, vue, svelte, ...)")
    error_signature: str = Field(
        ...,
        description="Normalized error message signature used for matching (e.g. 'Timeout waiting for #submit-btn')",
    )
    broken_selector: str = Field(
        ...,
        description="The original selector/locator that failed (e.g. '#submit-btn')",
    )
    fixed_selector: str = Field(
        ...,
        description="The selector/locator that resolved the failure (e.g. '#submit')",
    )
    patch_instructions: list[dict] = Field(
        default_factory=list,
        description="The full PatchInstruction list that was applied (for exact replay)",
    )
    dom_diff_summary: str = Field(
        default="",
        description="A short summary of what DOM attribute changed (e.g. 'id renamed submit-btn → submit')",
    )
    loop_count: int = Field(default=0, description="How many loops the original repair took")


class MemoryMatchResult(BaseModel):
    """Result of querying the healing-history store for similar past failures."""

    record: HealingRecord
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Similarity score (0.0–1.0); higher = more confident"
    )


class MemoryReport(BaseModel):
    """Machine-readable result of a memory lookup attempt."""

    hit: bool = Field(..., description="True when a confident match was found and applied")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    matched_record_id: str = Field(default="", description="ID of the matched HealingRecord")
    error: str = Field(default="", description="Human-readable message on miss or failure")


def _utc_timestamp_id() -> str:
    """Generate a simple UTC-based ID for records."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
