"""Contract tests: every emitted summary carries schema_version + a kind discriminator."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas import SCHEMA_VERSION, RepairSummary, ReviewReport, SuiteSummary

_AnySummary = RepairSummary | SuiteSummary | ReviewReport


def _repair(**overrides: Any) -> RepairSummary:
    fields: dict[str, Any] = {
        "test_script_path": "tests/login.spec.ts",
        "is_success": True,
        "loop_count": 1,
    }
    fields.update(overrides)
    return RepairSummary(**fields)


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


@pytest.mark.parametrize("summary", [_repair(), _suite(), _review()])
def test_all_output_models_share_one_schema_version(summary: _AnySummary) -> None:
    assert summary.schema_version == SCHEMA_VERSION


@pytest.mark.parametrize(
    ("summary", "expected_kind"),
    [
        (_repair(), "repair"),
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


@pytest.mark.parametrize("make", [_repair, _suite, _review])
def test_unsupported_schema_version_is_rejected(make: Callable[..., _AnySummary]) -> None:
    # The contract pins the emitted version, so a model can never serialize an
    # unsupported schema_version (e.g. a stale hard-coded "2.0").
    bad_version: Any = "2.0"
    with pytest.raises(ValidationError, match="schema_version"):
        make(schema_version=bad_version)


def test_suite_results_are_nested_repair_summaries() -> None:
    data = json.loads(_suite().model_dump_json())
    assert data["kind"] == "suite"
    assert [r["kind"] for r in data["results"]] == ["repair", "repair"]
    assert all(r["schema_version"] == SCHEMA_VERSION for r in data["results"])


def test_review_report_kind_is_review() -> None:
    data = json.loads(_review(has_findings=True).model_dump_json())
    assert data["kind"] == "review"
    assert data["schema_version"] == SCHEMA_VERSION


def test_review_report_marks_incomplete_provider_failures() -> None:
    data = json.loads(_review(is_complete=False, error="review provider failed").model_dump_json())
    assert data["is_complete"] is False
    assert data["error"] == "review provider failed"
