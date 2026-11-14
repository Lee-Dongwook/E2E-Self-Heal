"""Single boundary where repair evidence becomes model input.

Every prompt this package sends to a provider is assembled from a :class:`PreparedEvidence`,
so the failure log, the DOM diff, the ARIA snapshot and the Diagnoser's own report are redacted
exactly once, in one place, before any of them leaves the machine.

Test source code sent as context is deliberately **not** redacted: the Patch Generator returns
instructions whose ``original`` text must match the file byte for byte (``_apply`` in
``app/nodes/patch_generator.py``), so rewriting it would make instructions that can never
apply. Credentials do not belong in test sources, and the boundary covers the runtime evidence
that does carry them.
"""

from dataclasses import dataclass
from typing import cast

from app.preprocess.aria_snapshot import abstract_snapshot
from app.preprocess.jsx_chunker import extract_error_line
from app.shadow.redaction import RedactionRecord, redact_text, redact_value
from app.state import AgentState


@dataclass(frozen=True)
class PreparedEvidence:
    """Bounded, redacted evidence every prompt builder assembles its context from."""

    error_log: str
    dom_diff_context: list[dict]
    untouched_selector: str
    aria_snapshot: str
    analysis_report: str
    # Line number read from the *raw* log: redaction rewrites text, but not the line index the
    # JSX chunker needs.
    error_line: int | None
    redaction_count: int


def prepare_evidence(state: AgentState) -> PreparedEvidence:
    """Redact and bound the state fields that may be sent to a model provider.

    Every field is read with a default so a node running on a partial state (the Reviewer never
    needs the raw log) still gets an answer instead of a ``KeyError``.
    """
    record = RedactionRecord()
    raw_log = state.get("error_log") or ""
    error_log = redact_text(raw_log, record=record)
    dom_diff_context = cast(
        "list[dict]", redact_value(state.get("dom_diff_context", []), record=record)
    )
    untouched_selector = redact_text(state.get("untouched_selector") or "", record=record)
    snapshot = abstract_snapshot(state.get("dom_snapshot", ""))
    aria_snapshot = redact_text(snapshot, record=record) if snapshot else ""
    analysis_report = redact_text(state.get("analysis_report") or "", record=record)
    return PreparedEvidence(
        error_log=error_log,
        dom_diff_context=dom_diff_context,
        untouched_selector=untouched_selector,
        aria_snapshot=aria_snapshot,
        # The Diagnoser's report can quote the log, so it crosses the same boundary before
        # being re-sent to the Decline Adjudicator and the Patch Generator.
        analysis_report=analysis_report,
        error_line=extract_error_line(raw_log),
        redaction_count=record.count,
    )


def redaction_state_update(state: AgentState, prepared: PreparedEvidence) -> dict:
    """Return the accumulated state fields reporting that redaction happened."""
    total = state.get("redaction_count", 0) + prepared.redaction_count
    return {"redaction_count": total, "redaction_applied": total > 0}
