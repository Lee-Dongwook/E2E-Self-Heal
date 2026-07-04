"""Pydantic models: structured LLM output and machine-readable CI results."""

from pydantic import BaseModel, Field


class DomDiff(BaseModel):
    """A single before/after DOM node change parsed from a git diff."""

    file: str
    previous: dict = Field(default_factory=dict, description="DOM node before the change")
    current: dict = Field(default_factory=dict, description="DOM node after the change")


class PatchInstruction(BaseModel):
    """A single targeted edit produced by the Patch Generator.

    Scope is intentionally narrow: only failing locators and wait conditions.
    """

    line: int = Field(..., description="1-based line number to replace")
    original: str = Field(..., description="the exact line being replaced")
    replacement: str = Field(..., description="the new line content")
    reason: str = Field(..., description="why this selector/wait was changed")
    selector: str = Field(
        default="",
        description=(
            "the new locator as a Playwright selector-engine string usable by page.locator() "
            "(e.g. '#submit', 'role=button[name=\"Submit\"]', 'text=Submit'), for live-DOM "
            "verification. Empty if this edit is not a selector change (e.g. a wait tweak)."
        ),
    )


class PatchOutput(BaseModel):
    """Structured Output schema the LLM is forced to return (no free-form rewrites)."""

    instructions: list[PatchInstruction]


class RepairSummary(BaseModel):
    """Machine-readable result emitted for the CI wrapper to consume."""

    test_script_path: str
    is_success: bool
    loop_count: int
    instructions: list[PatchInstruction] = Field(default_factory=list)


class SuiteSummary(BaseModel):
    """Aggregate result when healing a whole suite (multiple failing tests)."""

    total_failed: int
    healed: int
    is_success: bool  # every failing test was healed
    results: list[RepairSummary] = Field(default_factory=list)
