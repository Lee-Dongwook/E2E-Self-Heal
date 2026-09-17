"""Pydantic models: structured LLM output and machine-readable CI results."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

# Version of the machine-readable CI contract emitted as `--json`. Version 2 adds the
# ``refusal`` discriminator and closed refusal reasons, which the published compatibility
# policy classifies as a breaking change.
SCHEMA_VERSION: Literal["2.0"] = "2.0"


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


class RefusalReason(str, Enum):
    """Closed taxonomy for why the repair workflow declined to make a change."""

    AMBIGUOUS_TARGET = "ambiguous_target"
    LIKELY_PRODUCT_REGRESSION = "likely_product_regression"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ARCHITECTURE_BOUNDARY_VIOLATION = "architecture_boundary_violation"
    GUARDRAIL_VIOLATION = "guardrail_violation"
    LOOP_CAP_REACHED = "loop_cap_reached"
    PROVIDER_ERROR = "provider_error"


class SnapshotReference(BaseModel):
    """Stable reference to a redacted failure-time ARIA snapshot."""

    sha256: str = Field(description="SHA-256 digest of the redacted snapshot content")
    source_path: str | None = Field(
        default=None,
        description="workspace-relative error-context.md source path, when available",
    )
    chars: int = Field(ge=0, description="character count of the redacted snapshot")


class CandidateEvidence(BaseModel):
    """One memory or LLM repair candidate and its verification evidence."""

    loop_count: int = Field(ge=0)
    source: Literal["memory", "llm"]
    instructions: list[PatchInstruction] = Field(default_factory=list)
    memory_score: float | None = Field(default=None, ge=0.0, le=1.0)
    shadow_score: float | None = None
    selector_counts: dict[str, int] = Field(default_factory=dict)
    test_passed: bool | None = None
    outcome: Literal["generated", "accepted", "rejected"] = "generated"
    rejection: str | None = None


class LoopEvidence(BaseModel):
    """One deterministic repair-loop stage outcome."""

    loop_count: int = Field(ge=0)
    stage: Literal[
        "memory_lookup",
        "patch_generator",
        "shadow_verifier",
        "selector_verifier",
        "test_runner",
        "refusal_finalizer",
    ]
    outcome: str
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class EvidenceBundle(BaseModel):
    """Reviewable, redacted evidence used to decide a repair or refusal."""

    parsed_error: str = ""
    failing_selector: str = ""
    dom_diff_context: list[dict] = Field(default_factory=list)
    aria_snapshot: SnapshotReference | None = None
    candidates: list[CandidateEvidence] = Field(default_factory=list)
    loop_history: list[LoopEvidence] = Field(default_factory=list)


class RefusalReport(BaseModel):
    """Machine-readable outcome when the repair workflow refuses to change a test."""

    schema_version: Literal["2.0"] = Field(
        default=SCHEMA_VERSION,
        description="version of this machine-readable contract; bump on breaking changes",
    )
    kind: Literal["refusal"] = Field(
        default="refusal",
        description="discriminator so consumers can dispatch without guessing on keys",
    )
    test_script_path: str = Field(
        ...,
        description="workspace-relative path to the test script the workflow declined to change",
    )
    reason: RefusalReason = Field(
        ...,
        description="closed taxonomy value explaining why the workflow refused the repair",
    )
    is_success: Literal[False] = Field(
        default=False,
        description="always false because this result records a declined repair",
    )
    loop_count: int = Field(ge=0, description="repair-loop count when the refusal was finalized")
    evidence: EvidenceBundle


class RepairSummary(BaseModel):
    """Machine-readable result emitted for the CI wrapper to consume."""

    schema_version: Literal["2.0"] = Field(
        default=SCHEMA_VERSION,
        description="version of this machine-readable contract; bump on breaking changes",
    )
    kind: Literal["repair"] = Field(
        default="repair",
        description="discriminator so consumers can dispatch without guessing on keys",
    )
    test_script_path: str
    is_success: bool
    loop_count: int
    instructions: list[PatchInstruction] = Field(default_factory=list)
    evidence: EvidenceBundle = Field(default_factory=EvidenceBundle)


HealResult = Annotated[RepairSummary | RefusalReport, Field(discriminator="kind")]


class SuiteSummary(BaseModel):
    """Aggregate result when healing a whole suite (multiple failing tests)."""

    schema_version: Literal["2.0"] = Field(
        default=SCHEMA_VERSION,
        description="version of this machine-readable contract; bump on breaking changes",
    )
    kind: Literal["suite"] = Field(
        default="suite",
        description="discriminator so consumers can dispatch without guessing on keys",
    )
    total_failed: int
    healed: int
    is_success: bool  # every failing test was healed
    results: list[HealResult] = Field(default_factory=list)


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

    schema_version: Literal["2.0"] = Field(
        default=SCHEMA_VERSION,
        description="version of this machine-readable contract; bump on breaking changes",
    )
    kind: Literal["review"] = Field(
        default="review",
        description="discriminator so consumers can dispatch without guessing on keys",
    )
    test_script_path: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    has_findings: bool = False
    is_complete: bool = Field(
        default=True,
        description="whether the review provider completed successfully",
    )
    error: str | None = Field(
        default=None,
        description="safe, user-facing reason when the review could not complete",
    )


class SelectorHint(BaseModel):
    """A stable selector hint provided by the user or Chrome Extension."""

    type: Literal["role", "testid", "text", "css"]
    value: str
    original: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
