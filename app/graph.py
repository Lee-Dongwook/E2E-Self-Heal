"""Assemble the repair StateGraph and its conditional Router edge."""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
import structlog

from app.config import settings
from app.evidence import add_loop_event
from app.nodes.diagnoser import diagnoser
from app.nodes.memory_lookup import memory_lookup
from app.nodes.patch_generator import patch_generator
from app.nodes.reviewer import reviewer
from app.nodes.selector_verifier import selector_verifier
from app.nodes.shadow_verifier import shadow_verifier
from app.nodes.test_runner import test_runner
from app.schemas import RefusalReason
from app.state import AgentState

logger = structlog.get_logger(__name__)
_REFUSAL_FINALIZER = "refusal_finalizer"


def route(state: AgentState) -> str:
    """Conditional edge: end on success or when the loop cap is hit, else re-diagnose."""
    if state["is_success"]:
        return END
    if state["loop_count"] >= settings.max_loops:
        return _REFUSAL_FINALIZER
    return "diagnoser"


def route_after_memory(state: AgentState) -> str:
    """Skip LLM work only when a guarded history candidate was applied."""
    if state.get("memory_report", {}).get("active", False):
        return "shadow_verifier"
    return "diagnoser"


def route_after_shadow(state: AgentState) -> str:
    """After shadow replay: proceed to live selector verifier on a pass/skip, else re-patch (or end at cap)."""
    report = state.get("shadow_report", {})
    if report.get("ok", True):
        return "selector_verifier"
    if state["loop_count"] >= settings.max_loops:
        return _REFUSAL_FINALIZER
    if state.get("memory_report", {}).get("active", False):
        return "diagnoser"
    return "patch_generator"


def refusal_finalizer(state: AgentState) -> dict:
    """Attach the most specific available reason to a terminal repair refusal."""
    application = state.get("patch_application_report", {})
    provider = state.get("patch_provider_report", {})
    verification = state.get("verification_report", {})

    if not state.get("boundary_report", {}).get("ok", True):
        # The path-policy check runs before a candidate exists, so it cannot be reported
        # as an assertion/control-flow guardrail rejection.
        reason = RefusalReason.ARCHITECTURE_BOUNDARY_VIOLATION
    elif application.get("guardrail_violation", False):
        reason = RefusalReason.GUARDRAIL_VIOLATION
    elif not provider.get("ok", True):
        # Exhausted structured-output retries directly establish provider failure; absent
        # diff context is weaker evidence and must not hide a known provider outage.
        reason = RefusalReason.PROVIDER_ERROR
    elif not verification.get("ok", True):
        # A failed live-DOM check proves this candidate is unusable, but does not prove a
        # product behavior change. Prefer the observed ambiguity over that speculation.
        reason = RefusalReason.AMBIGUOUS_TARGET
    elif not state["dom_diff_context"]:
        reason = RefusalReason.INSUFFICIENT_EVIDENCE
    else:
        reason = RefusalReason.LOOP_CAP_REACHED

    logger.info("repair_refused", reason=reason.value, loop_count=state["loop_count"])
    return {
        "refusal_reason": reason,
        "evidence_history": add_loop_event(
            state, "refusal_finalizer", "refused", reason=reason.value
        ),
    }


def route_after_patch(state: AgentState) -> str:
    """End on a permanent boundary violation; retry a stale line target before verification."""
    boundary_ok = state.get("boundary_report", {}).get("ok", True)
    application_ok = state.get("patch_application_report", {}).get("ok", True)
    # A boundary violation is permanent — the target path can't change mid-run, so retrying
    # the Patch Generator can never succeed. End immediately instead of burning loop budget.
    if not boundary_ok:
        return _REFUSAL_FINALIZER
    if not application_ok:
        if state["loop_count"] >= settings.max_loops:
            return _REFUSAL_FINALIZER
        return "patch_generator"
    return "shadow_verifier"


def route_after_verify(state: AgentState) -> str:
    """After verification: run the test if selectors hold, else re-patch (or end at cap).

    Shares the loop cap with ``route`` so the loop count stays the single termination
    budget; a rejected patch re-enters the Patch Generator rather than wasting a test run.
    """
    if state["verification_report"].get("ok", True):
        return "test_runner"
    if state["loop_count"] >= settings.max_loops:
        return _REFUSAL_FINALIZER
    if state.get("memory_report", {}).get("active", False):
        return "diagnoser"
    return "patch_generator"


def build_graph() -> CompiledStateGraph:
    """Build and compile the Diagnoser → Patch Generator → Shadow Verifier → Selector Verifier → Test Runner loop."""
    graph = StateGraph(AgentState)
    graph.add_node("memory_lookup", memory_lookup)
    graph.add_node("diagnoser", diagnoser)
    graph.add_node("patch_generator", patch_generator)
    graph.add_node("shadow_verifier", shadow_verifier)
    graph.add_node("selector_verifier", selector_verifier)
    graph.add_node("test_runner", test_runner)
    graph.add_node(_REFUSAL_FINALIZER, refusal_finalizer)

    graph.add_edge(START, "memory_lookup")
    graph.add_conditional_edges(
        "memory_lookup",
        route_after_memory,
        {"shadow_verifier": "shadow_verifier", "diagnoser": "diagnoser"},
    )
    graph.add_edge("diagnoser", "patch_generator")
    graph.add_conditional_edges(
        "patch_generator",
        route_after_patch,
        {
            "shadow_verifier": "shadow_verifier",
            "patch_generator": "patch_generator",
            _REFUSAL_FINALIZER: _REFUSAL_FINALIZER,
        },
    )
    graph.add_conditional_edges(
        "shadow_verifier",
        route_after_shadow,
        {
            "selector_verifier": "selector_verifier",
            "patch_generator": "patch_generator",
            "diagnoser": "diagnoser",
            _REFUSAL_FINALIZER: _REFUSAL_FINALIZER,
        },
    )
    graph.add_conditional_edges(
        "selector_verifier",
        route_after_verify,
        {
            "test_runner": "test_runner",
            "patch_generator": "patch_generator",
            "diagnoser": "diagnoser",
            _REFUSAL_FINALIZER: _REFUSAL_FINALIZER,
        },
    )
    graph.add_conditional_edges(
        "test_runner",
        route,
        {"diagnoser": "diagnoser", _REFUSAL_FINALIZER: _REFUSAL_FINALIZER, END: END},
    )
    graph.add_edge(_REFUSAL_FINALIZER, END)

    return graph.compile()


def build_review_graph() -> CompiledStateGraph:
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
