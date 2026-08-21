from langgraph.graph import END

from app.config import settings
from app.graph import build_graph, route, route_after_memory, route_after_shadow, route_after_verify
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
    assert route(_state(loop_count=settings.max_loops)) == END


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


def test_graph_compiles():
    assert build_graph() is not None
