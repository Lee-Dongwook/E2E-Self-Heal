"""Pydantic models: structured LLM output and machine-readable CI results."""

from enum import Enum
from typing import Annotated, Literal, TypedDict, cast

from pydantic import BaseModel, Field, computed_field

# Version of the machine-readable CI contract emitted as `--json`. Version 2 adds the
# ``refusal`` discriminator and closed refusal reasons, which the published compatibility
# policy classifies as a breaking change. Version 2.1 adds the optional
# ``CandidateEvidence.ast_lock`` detail object, a compatible minor addition. Version 2.2 adds
# the optional ``EvidenceBundle.provenance`` object, another compatible minor addition. Version
# 2.3 adds the executed graph ``path``, the ``loop_budget``, ``CandidateEvidence.next_step`` and
# the ``termination`` of a repair or refusal: optional additions, so a minor bump as well.
SCHEMA_VERSION: Literal["2.3"] = "2.3"

# How a repair run ended. ``refused`` covers every refusal reason except the loop cap, which
# splits by what the last candidate ran into: a verifier (``verification_failed``) or nothing a
# verifier could judge (``budget_exhausted``).
Termination = Literal["verified", "refused", "budget_exhausted", "verification_failed"]

# Candidate rejections that come from running a verifier on the patched test, as opposed to the
# line gates, the AST lock or an application error that stop a candidate before verification.
VERIFIER_REJECTIONS = frozenset(
    {"shadow_failed", "shadow_error", "selector_not_unique", "test_failed", "final_suite_failed"}
)


class DomDiff(BaseModel):
    """A single before/after DOM node change parsed from a git diff."""

    file: str
    line: int = Field(
        default=0,
        description="1-based line in the new file where the changed element sits (0 if unknown)",
    )
    previous: dict = Field(default_factory=dict, description="DOM node before the change")
    current: dict = Field(default_factory=dict, description="DOM node after the change")


class DomNode(TypedDict, total=False):
    """A JSX DOM node captured on one side of a changed element."""

    tag: str
    attributes: dict[str, str]


class EvidenceDomDiff(BaseModel):
    """A typed DOM diff entry published in repair/refusal evidence."""

    file: str
    line: int = Field(ge=0)
    previous: DomNode = Field(default_factory=lambda: cast(DomNode, {}))
    current: DomNode = Field(default_factory=lambda: cast(DomNode, {}))


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


class DiagnosisVerdict(str, Enum):
    """What broke the test, as a closed set the decline adjudicator must choose from.

    Only ``BEHAVIOUR_CHANGE`` changes the outcome: it is what turns a refusal into
    ``RefusalReason.LIKELY_PRODUCT_REGRESSION``. The other two keep the ordinary repair loop,
    so a wrong verdict of that kind costs a model call, never a wrong label.
    """

    SELECTOR_DRIFT = "selector_drift"
    BEHAVIOUR_CHANGE = "behaviour_change"
    UNCLEAR = "unclear"


class DiagnosisVerdictOutput(BaseModel):
    """Structured Output schema for the adjudication of a declined patch.

    Internal to the repair loop: it is never emitted in the ``--json`` contract, so adding or
    changing it is not a schema version change. ``rationale`` is required so the model has to
    commit to a reason before naming a verdict; it is not otherwise consumed.
    """

    verdict: DiagnosisVerdict
    rationale: str = Field(min_length=1, description="why this verdict fits the evidence")


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

    sha256: str = Field(description="SHA-256 digest of the redacted extracted snapshot content")
    source_path: str | None = Field(
        default=None,
        description="workspace-relative error-context.md provenance path, when available",
    )
    chars: int = Field(ge=0, description="character count of the redacted snapshot")


class AstLockRejection(BaseModel):
    """What the structural AST lock rejected in a refused candidate.

    ``reason`` is an open string, not a closed enum: the lock may report new reasons without
    a schema major bump, so consumers must tolerate values they do not recognize. Current
    values are ``disallowed_ast_change``, ``original_parse_error`` and ``patched_parse_error``.
    """

    reason: str = Field(description="why the lock rejected the candidate")
    node_kind: str | None = Field(
        default=None,
        description="tree-sitter node type of the first offending node, when one is known",
    )
    line: int | None = Field(
        default=None,
        ge=1,
        description="1-based line of the offending node in the patched (or unparsable) source",
    )


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
    ast_lock: AstLockRejection | None = Field(
        default=None,
        description=(
            "structured AST lock verdict when the lock rejected this candidate; absent for "
            "rejections from other checks, whose cause is only in ``rejection``"
        ),
    )
    next_step: str | None = Field(
        default=None,
        description=(
            "graph node the run moved to once this candidate was accepted or rejected, or "
            "``end`` when the run stopped there; null while it was still undecided. An open "
            "string: nodes can be added without a contract change"
        ),
    )


class LoopEvidenceDetails(BaseModel):
    """Known, sanitized details emitted by repair-loop stages."""

    score: float | None = None
    error: str | None = None
    instruction_count: int | None = Field(default=None, ge=0)
    reason: RefusalReason | None = None


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
    details: LoopEvidenceDetails = Field(default_factory=LoopEvidenceDetails)


class GraphStep(BaseModel):
    """One node the repair graph executed, in execution order."""

    node: str = Field(description="graph node name; an open string, nodes can be added")
    loop_count: int = Field(ge=0, description="loop count after the node ran")


class SelectorHintEvidence(BaseModel):
    """A user-provided selector hint preserved in repair evidence."""

    type: Literal["selector_hint"]
    hint_type: Literal["role", "testid", "text", "css"]
    value: str
    original: str
    confidence: float = Field(ge=0.0, le=1.0)
    priority: Literal["high"]


class ProviderProvenance(BaseModel):
    """Where the repair evidence was sent, and whether it was redacted first.

    Deliberately non-sensitive: no prompt text, no response text, no credential. ``provider``
    is an open string rather than a closed enum so adding a backend is not a contract change;
    consumers must tolerate values they do not recognize. ``endpoint`` is the endpoint the
    client actually used, with URL credentials and sensitive query values removed.
    """

    provider: str = Field(description="configured provider backend (an open, growing set)")
    model: str = Field(description="model name the provider was asked to run")
    endpoint: str = Field(description="redacted endpoint evidence was sent to")
    mode: Literal["local", "remote"] = Field(
        description=(
            "local when the endpoint host is loopback or a private/link-local address, "
            "otherwise remote; derived from the address, never from DNS"
        )
    )
    redaction_applied: bool = Field(
        description="whether the evidence boundary replaced at least one value in a prompt"
    )
    redaction_count: int = Field(
        ge=0, description="number of values the evidence boundary replaced while prompting"
    )


class EvidenceBundle(BaseModel):
    """Reviewable, redacted evidence used to decide a repair or refusal."""

    parsed_error: str = ""
    failing_selector: str = ""
    dom_diff_context: list[EvidenceDomDiff | SelectorHintEvidence] = Field(default_factory=list)
    aria_snapshot: SnapshotReference | None = None
    candidates: list[CandidateEvidence] = Field(default_factory=list)
    loop_history: list[LoopEvidence] = Field(default_factory=list)
    provenance: ProviderProvenance | None = Field(
        default=None,
        description=(
            "provider, model, and endpoint that saw the evidence; absent when the graph never "
            "ran, because then nothing was sent to a provider"
        ),
    )
    path: list[GraphStep] = Field(
        default_factory=list,
        description=(
            "every node the repair graph executed, in order; each adjacent pair is a routing "
            "decision. Empty when the graph never ran"
        ),
    )
    loop_budget: int | None = Field(
        default=None,
        ge=1,
        description="loop cap the run was allowed (E2E_HEALER_MAX_LOOPS); null when it never ran",
    )


class RefusalReport(BaseModel):
    """Machine-readable outcome when the repair workflow refuses to change a test."""

    schema_version: Literal["2.3"] = Field(
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

    @computed_field
    @property
    def termination(self) -> Termination:
        """How the run ended: a refusal on evidence, or the loop cap with what stopped it."""
        if self.reason is not RefusalReason.LOOP_CAP_REACHED:
            return "refused"
        candidates = self.evidence.candidates
        if candidates and candidates[-1].rejection in VERIFIER_REJECTIONS:
            return "verification_failed"
        return "budget_exhausted"


class RepairSummary(BaseModel):
    """Machine-readable result emitted for the CI wrapper to consume."""

    schema_version: Literal["2.3"] = Field(
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

    @computed_field
    @property
    def termination(self) -> Termination:
        """``verified``, or ``verification_failed`` once the final suite rerun rejected it."""
        return "verified" if self.is_success else "verification_failed"


HealResult = Annotated[RepairSummary | RefusalReport, Field(discriminator="kind")]


class SuiteSummary(BaseModel):
    """Aggregate result when healing a whole suite (multiple failing tests)."""

    schema_version: Literal["2.3"] = Field(
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

    schema_version: Literal["2.3"] = Field(
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
