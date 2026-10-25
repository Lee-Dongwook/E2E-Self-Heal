import pytest
from langgraph.graph import END

import app.graph as graph_module
from app.config import settings
from app.graph import (
    declined_candidate,
    refusal_finalizer,
    route,
    route_after_decline,
    route_after_memory,
    route_after_patch,
    route_after_shadow,
    route_after_verify,
)
from app.schemas import DiagnosisVerdict, RefusalReason
from app.state import AgentState


def _state(**overrides) -> AgentState:
    base: AgentState = {
        "test_script_path": "t.spec.ts",
        "original_code": "",
        "current_code": "",
        "error_log": "",
        "dom_diff_context": [],
        "dom_snapshot": "",
        "analysis_report": "",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"is_success": True}, END),
        ({"loop_count": settings.max_loops}, "refusal_finalizer"),
        ({"is_success": False, "loop_count": 0}, "diagnoser"),
    ],
    ids=["success", "loop-cap", "retry"],
)
def test_route_selects_terminal_or_retry_path(overrides: dict, expected: str) -> None:
    assert route(_state(**overrides)) == expected


def test_memory_hit_starts_verification_and_rejection_retries_diagnosis() -> None:
    memory_state = _state(memory_report={"active": True}, shadow_report={"ok": False})

    assert route_after_memory(memory_state) == "shadow_verifier"
    assert route_after_shadow(memory_state) == "diagnoser"
    assert (
        route_after_verify(
            _state(memory_report={"active": True}, verification_report={"ok": False})
        )
        == "diagnoser"
    )


def test_capped_memory_verification_failures_finalize_immediately() -> None:
    shadow_state = _state(
        loop_count=settings.max_loops,
        memory_report={"active": True},
        shadow_report={"ok": False},
    )
    selector_state = _state(
        loop_count=settings.max_loops,
        memory_report={"active": True},
        verification_report={"ok": False},
    )

    assert route_after_shadow(shadow_state) == "refusal_finalizer"
    assert route_after_verify(selector_state) == "refusal_finalizer"


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        (
            {"boundary_report": {"ok": False}},
            RefusalReason.ARCHITECTURE_BOUNDARY_VIOLATION,
        ),
        (
            {"patch_application_report": {"ok": False, "guardrail_violation": True}},
            RefusalReason.GUARDRAIL_VIOLATION,
        ),
        ({"patch_provider_report": {"ok": False}}, RefusalReason.PROVIDER_ERROR),
        ({"verification_report": {"ok": False}}, RefusalReason.AMBIGUOUS_TARGET),
        ({"dom_diff_context": []}, RefusalReason.INSUFFICIENT_EVIDENCE),
        (
            {
                "dom_diff_context": [{"file": "src/Login.tsx"}],
                "diagnosis_verdict": DiagnosisVerdict.BEHAVIOUR_CHANGE,
            },
            RefusalReason.LIKELY_PRODUCT_REGRESSION,
        ),
        ({"dom_diff_context": [{"file": "src/Login.tsx"}]}, RefusalReason.LOOP_CAP_REACHED),
    ],
)
def test_refusal_finalizer_selects_the_most_specific_reason(
    overrides: dict, expected_reason: RefusalReason
) -> None:
    result = refusal_finalizer(_state(**overrides))

    assert result["refusal_reason"] is expected_reason


def test_provider_failure_takes_precedence_over_missing_context() -> None:
    result = refusal_finalizer(_state(patch_provider_report={"ok": False}, dom_diff_context=[]))

    assert result["refusal_reason"] is RefusalReason.PROVIDER_ERROR


def test_route_after_patch_finalizes_permanent_boundary_rejections():
    assert route_after_patch(_state(boundary_report={"ok": False})) == "refusal_finalizer"


def test_route_after_patch_adjudicates_a_candidate_that_proposed_nothing() -> None:
    declined = _state(
        patch_provider_report={"ok": True},
        patch_instructions={"instructions": []},
    )
    proposed = _state(
        patch_provider_report={"ok": True},
        patch_instructions={"instructions": [{"line": 1}]},
    )

    assert route_after_patch(declined) == "decline_adjudicator"
    assert route_after_patch(proposed) == "shadow_verifier"


def test_a_provider_failure_is_not_a_declined_candidate() -> None:
    # A failed call leaves no instructions either; interpreting that as a decline would let an
    # outage be labelled as a product regression.
    failed = _state(patch_provider_report={"ok": False}, patch_instructions={})

    assert declined_candidate(failed) is False
    assert route_after_patch(failed) == "shadow_verifier"


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (DiagnosisVerdict.BEHAVIOUR_CHANGE, "refusal_finalizer"),
        (None, "shadow_verifier"),
        (DiagnosisVerdict.UNCLEAR, "shadow_verifier"),
        (DiagnosisVerdict.SELECTOR_DRIFT, "shadow_verifier"),
    ],
    ids=["behaviour-change", "provider-failure", "unclear", "selector-drift"],
)
def test_route_after_decline(verdict: DiagnosisVerdict | None, expected: str) -> None:
    overrides = {} if verdict is None else {"diagnosis_verdict": verdict}
    assert route_after_decline(_state(**overrides)) == expected


def test_a_missing_diff_outranks_a_behaviour_change_verdict() -> None:
    # No DOM change means the run cannot support a claim about product behaviour.
    result = refusal_finalizer(
        _state(dom_diff_context=[], diagnosis_verdict=DiagnosisVerdict.BEHAVIOUR_CHANGE)
    )

    assert result["refusal_reason"] is RefusalReason.INSUFFICIENT_EVIDENCE


def test_an_observed_guardrail_or_ambiguity_outranks_a_behaviour_change_verdict() -> None:
    overrides = {
        "dom_diff_context": [{"file": "src/Login.tsx"}],
        "diagnosis_verdict": DiagnosisVerdict.BEHAVIOUR_CHANGE,
    }

    guardrail = refusal_finalizer(
        _state(**overrides, patch_application_report={"ok": False, "guardrail_violation": True})
    )
    ambiguous = refusal_finalizer(_state(**overrides, verification_report={"ok": False}))

    assert guardrail["refusal_reason"] is RefusalReason.GUARDRAIL_VIOLATION
    assert ambiguous["refusal_reason"] is RefusalReason.AMBIGUOUS_TARGET


def test_an_ambiguous_target_is_refused_before_any_node_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_run(state: AgentState) -> dict:
        raise AssertionError("no repair work may run on an ambiguous target")

    for node in ("memory_lookup", "diagnoser", "patch_generator", "test_runner"):
        monkeypatch.setattr(graph_module, node, must_not_run)
    initial = _state(
        dom_diff_context=[{"file": "src/SubmitButton.tsx"}],
        ambiguous_candidates=["#save-draft", "#publish"],
        evidence_history=[],
    )

    final = graph_module.build_graph().invoke(initial)

    assert final["refusal_reason"] is RefusalReason.AMBIGUOUS_TARGET
    assert final["loop_count"] == 0
    assert final["current_code"] == initial["current_code"]
