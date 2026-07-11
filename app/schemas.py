"""Pydantic models: structured LLM output and machine-readable CI results."""

from typing import Literal

from pydantic import BaseModel, Field


class DomDiff(BaseModel):
    """A single before/after DOM node change parsed from a git diff."""

    file: str
    line: int = Field(
        default=0,
        description="1-based line in the new file where the changed element sits (0 if unknown)",
    )
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


class ReviewFinding(BaseModel):
    """A single source-level suggestion produced by the Reviewer (review mode).

    Advisory only: the review mode never edits the test. A finding anchors to the changed
    *source* file/line so the CI wrapper can post it as an inline PR comment.
    """

    file: str = Field(..., description="source file that changed (e.g. components/CTAButton.tsx)")
    line: int = Field(..., description="1-based line in the new source file to comment on")
    broken_selector: str = Field(..., description="the test locator that no longer matches")
    root_cause: str = Field(..., description="which DOM attribute change broke the selector")
    suggestion: str = Field(
        ...,
        description="source-level fix (e.g. add a stable data-testid or an accessible role/name)",
    )
    recommended_selector: str = Field(
        default="",
        description="accessibility-first test selector to prefer (e.g. getByRole('button', ...))",
    )
    severity: Literal["info", "warning"] = Field(
        default="warning", description="advisory severity for the PR comment"
    )


class ReviewOutput(BaseModel):
    """Structured Output schema the Reviewer LLM is forced to return (no free-form prose)."""

    findings: list[ReviewFinding]


class ReviewReport(BaseModel):
    """Machine-readable review result emitted for the CI wrapper to post as PR comments."""

    test_script_path: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    has_findings: bool = False
