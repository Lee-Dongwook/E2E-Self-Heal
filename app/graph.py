"""Assemble the repair StateGraph and its conditional Router edge."""

from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.nodes.diagnoser import diagnoser
from app.nodes.patch_generator import patch_generator
from app.nodes.reviewer import reviewer
from app.nodes.selector_verifier import selector_verifier
from app.nodes.test_runner import test_runner
from app.state import AgentState


def route(state: AgentState) -> str:
    """Conditional edge: end on success or when the loop cap is hit, else re-diagnose."""
    if state["is_success"] or state["loop_count"] >= settings.max_loops:
        return END
    return "diagnoser"


def route_after_verify(state: AgentState) -> str:
    """After verification: run the test if selectors hold, else re-patch (or end at cap).

    Shares the loop cap with ``route`` so the loop count stays the single termination
    budget; a rejected patch re-enters the Patch Generator rather than wasting a test run.
    """
    if state["verification_report"].get("ok", True):
        return "test_runner"
    if state["loop_count"] >= settings.max_loops:
        return END
    return "patch_generator"


def build_graph():
    """Build and compile the Diagnoser → Patch Generator → Selector Verifier → Test Runner loop."""
    graph = StateGraph(AgentState)
    graph.add_node("diagnoser", diagnoser)
    graph.add_node("patch_generator", patch_generator)
    graph.add_node("selector_verifier", selector_verifier)
    graph.add_node("test_runner", test_runner)

    graph.add_edge(START, "diagnoser")
    graph.add_edge("diagnoser", "patch_generator")
    graph.add_edge("patch_generator", "selector_verifier")
    graph.add_conditional_edges(
        "selector_verifier",
        route_after_verify,
        {"test_runner": "test_runner", "patch_generator": "patch_generator", END: END},
    )
    graph.add_conditional_edges("test_runner", route, {"diagnoser": "diagnoser", END: END})

    return graph.compile()


def build_review_graph():
    """Build the review-mode graph: Diagnoser → Reviewer → END.

    Reuses the Diagnoser to infer the root cause, then advises a source-level fix. No patch,
    verify, test-run, or loop — review mode is strictly read-only and advisory.
    """
    graph = StateGraph(AgentState)
    graph.add_node("diagnoser", diagnoser)
    graph.add_node("reviewer", reviewer)

    graph.add_edge(START, "diagnoser")
    graph.add_edge("diagnoser", "reviewer")
    graph.add_edge("reviewer", END)

    return graph.compile()
