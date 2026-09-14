import pytest
from langgraph.graph import END

from app.config import settings
from app.graph import (
    build_graph,
    refusal_finalizer,
    route,
    route_after_memory,
    route_after_patch,
    route_after_shadow,
    route_after_verify,
)
from app.schemas import RefusalReason
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


def test_route_ends_on_success():
    assert route(_state(is_success=True)) == END


def test_route_ends_on_loop_cap():
    assert route(_state(loop_count=settings.max_loops)) == "refusal_finalizer"


def test_route_continues_when_failing_under_cap():
    assert route(_state(is_success=False, loop_count=0)) == "diagnoser"


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


def test_graph_compiles():
    assert build_graph() is not None


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


def test_route_after_patch_finalizes_permanent_boundary_rejections() -> None:
    assert route_after_patch(_state(boundary_report={"ok": False})) == "refusal_finalizer"
