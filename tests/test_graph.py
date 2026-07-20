from langgraph.graph import END

from app.config import settings
from app.graph import build_graph, route, route_after_memory
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


def test_graph_compiles():
    assert build_graph() is not None


# --- Memory routing tests (issue #120) ---


def test_route_after_memory_goes_to_test_runner_on_hit():
    assert route_after_memory(_state(memory_report={"hit": True})) == "test_runner"


def test_route_after_memory_goes_to_diagnoser_on_miss():
    assert route_after_memory(_state(memory_report={"hit": False})) == "diagnoser"


def test_route_after_memory_goes_to_diagnoser_when_no_report():
    assert route_after_memory(_state()) == "diagnoser"
