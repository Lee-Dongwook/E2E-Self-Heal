"""Build and maintain the redacted evidence emitted for repair outcomes."""

import hashlib
from pathlib import Path
from typing import Literal, cast

from app.healing_history import extract_failing_selector
from app.sandbox import workspace_root
from app.schemas import (
    CandidateEvidence,
    EvidenceBundle,
    LoopEvidence,
    PatchInstruction,
    SnapshotReference,
)
from app.shadow.redaction import redact_value
from app.state import AgentState

EvidenceStage = Literal[
    "memory_lookup",
    "patch_generator",
    "shadow_verifier",
    "selector_verifier",
    "test_runner",
    "refusal_finalizer",
]


def add_loop_event(
    state: AgentState,
    stage: EvidenceStage,
    outcome: str,
    **details: str | int | float | bool | None,
) -> list[dict]:
    """Append one sanitized stage result without mutating graph state in place."""
    history = [dict(event) for event in state.get("evidence_history", [])]
    history.append(
        {
            "loop_count": state["loop_count"],
            "stage": stage,
            "outcome": outcome,
            "details": cast(dict, redact_value(details)),
        }
    )
    return history


def add_candidate(
    state: AgentState,
    *,
    source: Literal["memory", "llm"],
    instructions: list[PatchInstruction],
    memory_score: float | None = None,
    outcome: Literal["generated", "accepted", "rejected"] = "generated",
    rejection: str | None = None,
) -> list[dict]:
    """Record a candidate in order so a later retry cannot discard it."""
    candidates = [dict(candidate) for candidate in state.get("evidence_candidates", [])]
    candidates.append(
        cast(
            dict,
            redact_value(
                {
                    "loop_count": state["loop_count"],
                    "source": source,
                    "instructions": [instruction.model_dump() for instruction in instructions],
                    "memory_score": memory_score,
                    "outcome": outcome,
                    "rejection": rejection,
                }
            ),
        )
    )
    return candidates


def update_latest_candidate(state: AgentState, **updates: object) -> list[dict]:
    """Return a copied candidate list with the newest candidate augmented by a verifier."""
    candidates = [dict(candidate) for candidate in state.get("evidence_candidates", [])]
    if candidates:
        candidates[-1].update(cast(dict, redact_value(updates)))
    return candidates


def _snapshot_reference(snapshot: str, source: str | None) -> SnapshotReference | None:
    if not snapshot:
        return None
    redacted_snapshot = cast(str, redact_value(snapshot))
    source_path = None
    if source:
        try:
            source_path = Path(source).resolve().relative_to(workspace_root()).as_posix()
        except ValueError:
            source_path = None
    return SnapshotReference(
        sha256=hashlib.sha256(redacted_snapshot.encode("utf-8")).hexdigest(),
        source_path=source_path,
        chars=len(redacted_snapshot),
    )


def build_evidence_bundle(state: AgentState, *, initial_error_log: str) -> EvidenceBundle:
    """Convert trace state to the stable, sanitized public evidence model."""
    parsed_error = cast(str, redact_value(initial_error_log))
    dom_diff_context = cast(list[dict], redact_value(state["dom_diff_context"]))
    candidates = [
        CandidateEvidence.model_validate(redact_value(candidate))
        for candidate in state.get("evidence_candidates", [])
    ]
    loop_history = [
        LoopEvidence.model_validate(redact_value(event))
        for event in state.get("evidence_history", [])
    ]
    return EvidenceBundle(
        parsed_error=parsed_error,
        failing_selector=extract_failing_selector(parsed_error),
        dom_diff_context=dom_diff_context,
        aria_snapshot=_snapshot_reference(
            state.get("dom_snapshot", ""), state.get("dom_snapshot_source")
        ),
        candidates=candidates,
        loop_history=loop_history,
    )


def build_unavailable_evidence(parsed_error: str, dom_diff_context: list[dict]) -> EvidenceBundle:
    """Build initial evidence when a suite target is unavailable to the repair graph."""
    safe_error = cast(str, redact_value(parsed_error))
    return EvidenceBundle(
        parsed_error=safe_error,
        failing_selector=extract_failing_selector(safe_error),
        dom_diff_context=cast(list[dict], redact_value(dom_diff_context)),
    )
