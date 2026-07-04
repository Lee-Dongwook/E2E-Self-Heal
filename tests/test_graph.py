from langgraph.graph import END

from app.config import settings
from app.graph import build_graph, route
from app.state import AgentState


def _state(**overrides) -> AgentState:
    base: AgentState = {
        "test_script_path": "t.spec.ts",
        "original_code": "",
        "current_code": "",
        "error_log": "",
        "dom_diff_context": [],
        "analysis_report": "",
        "patch_instructions": {},
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
