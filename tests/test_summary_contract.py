"""Contract tests: every emitted summary carries schema_version + a kind discriminator."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas import (
    AstLockRejection,
    CandidateEvidence,
    RefusalReason,
    RefusalReport,
    EvidenceBundle,
    SCHEMA_VERSION,
    RepairSummary,
    ReviewReport,
    SuiteSummary,
)

_AnySummary = RefusalReport | RepairSummary | SuiteSummary | ReviewReport


def _repair(**overrides: Any) -> RepairSummary:
    fields: dict[str, Any] = {
        "test_script_path": "tests/login.spec.ts",
        "is_success": True,
        "loop_count": 1,
    }
    fields.update(overrides)
    return RepairSummary(**fields)


def _refusal(**overrides: Any) -> RefusalReport:
    fields: dict[str, Any] = {
        "test_script_path": "tests/login.spec.ts",
        "reason": RefusalReason.AMBIGUOUS_TARGET,
        "loop_count": 1,
        "evidence": EvidenceBundle(),
    }
    fields.update(overrides)
    return RefusalReport(**fields)


def _suite(**overrides: Any) -> SuiteSummary:
    return SuiteSummary(
        total_failed=2,
        healed=1,
        is_success=False,
        results=[_repair(), _repair(is_success=False, loop_count=3)],
        **overrides,
    )


def _review(**overrides: Any) -> ReviewReport:
    return ReviewReport(test_script_path="tests/login.spec.ts", **overrides)


@pytest.mark.parametrize(
    ("summary", "expected_kind"),
    [
        (_repair(), "repair"),
        (_refusal(), "refusal"),
        (_suite(), "suite"),
        (_review(), "review"),
    ],
)
def test_model_json_is_self_describing(summary: _AnySummary, expected_kind: str) -> None:
    data = json.loads(summary.model_dump_json())
    assert data["kind"] == expected_kind
    assert data["schema_version"] == SCHEMA_VERSION


def test_kind_is_a_fixed_literal() -> None:
    bad_kind: Any = "nope"
    with pytest.raises(ValidationError):
        _repair(kind=bad_kind)


def test_refusal_report_round_trips_through_json() -> None:
    report = _refusal(reason=RefusalReason.ARCHITECTURE_BOUNDARY_VIOLATION)

    restored = RefusalReport.model_validate_json(report.model_dump_json())

    assert restored == report
    assert json.loads(report.model_dump_json())["reason"] == "architecture_boundary_violation"
    assert report.is_success is False


def test_refusal_report_rejects_unknown_reason() -> None:
    with pytest.raises(ValidationError, match="reason"):
        _refusal(reason="unknown_reason")


@pytest.mark.parametrize("make", [_repair, _suite, _review])
@pytest.mark.parametrize("bad_version", ["1.0", "2.0", "2.1", "2.2"])
def test_unsupported_schema_version_is_rejected(
    make: Callable[..., _AnySummary], bad_version: str
) -> None:
    # The contract pins the emitted version, so a model can never serialize a stale or
    # unsupported schema_version (e.g. a hard-coded "2.2" after the 2.3 minor bump).
    with pytest.raises(ValidationError, match="schema_version"):
        make(schema_version=bad_version)


def test_optional_detail_objects_are_minor_additions_to_the_2_major() -> None:
    # Policy: an optional detail object is a compatible minor change, so the major stays 2.
    assert SCHEMA_VERSION == "2.3"
    assert SCHEMA_VERSION.split(".")[0] == "2"


def test_evidence_bundle_from_an_older_minor_still_validates() -> None:
    # Additive-change guarantee: evidence emitted before 2.2 has no provenance key and before
    # 2.3 no path or loop budget. A consumer must read that as absence rather than as an error.
    bundle = EvidenceBundle.model_validate({"parsed_error": "boom"})

    assert bundle.provenance is None
    assert bundle.path == []
    assert bundle.loop_budget is None
    assert json.loads(bundle.model_dump_json())["provenance"] is None


def test_refusal_json_carries_structured_ast_lock_detail() -> None:
    report = _refusal(
        reason=RefusalReason.GUARDRAIL_VIOLATION,
        evidence=EvidenceBundle(
            candidates=[
                CandidateEvidence(
                    loop_count=0,
                    source="llm",
                    outcome="rejected",
                    rejection="AST lock rejected the patch",
                    ast_lock=AstLockRejection(
                        reason="disallowed_ast_change", node_kind="expression_statement", line=4
                    ),
                )
            ]
        ),
    )

    data = json.loads(report.model_dump_json())

    assert data["reason"] == "guardrail_violation"
    assert data["evidence"]["candidates"][0]["ast_lock"] == {
        "reason": "disallowed_ast_change",
        "node_kind": "expression_statement",
        "line": 4,
    }
    assert RefusalReport.model_validate_json(report.model_dump_json()) == report


def test_candidate_payload_without_ast_lock_still_validates() -> None:
    # Additive-change guarantee: a candidate emitted before 2.1 has no ast_lock key, and a
    # consumer's model of it must read that as absence rather than as an error.
    candidate = CandidateEvidence.model_validate(
        {"loop_count": 1, "source": "llm", "outcome": "rejected", "rejection": "stale line"}
    )

    assert candidate.ast_lock is None


def test_ast_lock_rejection_tolerates_unknown_reasons_but_not_a_zero_line() -> None:
    # `reason` is deliberately open so new lock reasons are not a major bump.
    assert AstLockRejection(reason="a_future_reason").node_kind is None
    with pytest.raises(ValidationError, match="line"):
        AstLockRejection(reason="disallowed_ast_change", line=0)


def test_suite_results_are_nested_repair_summaries() -> None:
    data = json.loads(_suite().model_dump_json())
    assert data["kind"] == "suite"
    assert [r["kind"] for r in data["results"]] == ["repair", "repair"]
    assert all(r["schema_version"] == SCHEMA_VERSION for r in data["results"])


def test_review_report_marks_incomplete_provider_failures() -> None:
    data = json.loads(_review(is_complete=False, error="review provider failed").model_dump_json())
    assert data["is_complete"] is False
    assert data["error"] == "review provider failed"
