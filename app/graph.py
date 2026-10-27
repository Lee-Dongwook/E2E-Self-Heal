"""Assemble the repair StateGraph and its conditional Router edge."""

from typing import Protocol, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
import structlog

from app.config import settings
from app.evidence import add_loop_event
from app.nodes.decline_adjudicator import decline_adjudicator
from app.nodes.diagnoser import diagnoser
from app.nodes.memory_lookup import memory_lookup
from app.nodes.patch_generator import patch_generator
from app.nodes.reviewer import reviewer
from app.nodes.selector_verifier import selector_verifier
from app.nodes.shadow_verifier import shadow_verifier
from app.nodes.test_runner import test_runner
from app.schemas import DiagnosisVerdict, RefusalReason
from app.state import AgentState, EvidenceCandidateRecord, GraphStepRecord

logger = structlog.get_logger(__name__)
_REFUSAL_FINALIZER = "refusal_finalizer"
_DECLINE_ADJUDICATOR = "decline_adjudicator"
_DECIDED = ("accepted", "rejected")


class Node(Protocol):
    """A repair-graph node: reads the state and returns a partial update."""

    def __call__(self, state: AgentState) -> dict: ...


def route(state: AgentState) -> str:
    """Conditional edge: end on success or when the loop cap is hit, else re-diagnose."""
    if state["is_success"]:
        return END
    if state["loop_count"] >= settings.max_loops:
        return _REFUSAL_FINALIZER
    return "diagnoser"


def route_at_start(state: AgentState) -> str:
    """Refuse an ambiguous target before any work; otherwise start with the memory lookup.

    The ambiguity is read from the diff, not judged by a model, and no candidate can resolve
    it, so spending a loop (or a remembered repair) on it would only produce a guess.
    """
    if state.get("ambiguous_candidates"):
        return _REFUSAL_FINALIZER
    return "memory_lookup"


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
    elif not verification.get("ok", True) or state.get("ambiguous_candidates"):
        # A failed live-DOM check proves this candidate is unusable, but does not prove a
        # product behavior change. Prefer the observed ambiguity over that speculation. The
        # diff-level check (several look-alikes replaced the failing element) is the same
        # observation made before any candidate exists.
        reason = RefusalReason.AMBIGUOUS_TARGET
    elif not state["dom_diff_context"]:
        reason = RefusalReason.INSUFFICIENT_EVIDENCE
    elif state.get("diagnosis_verdict") is DiagnosisVerdict.BEHAVIOUR_CHANGE:
        # The model declined to patch and, asked directly, judged that no locator or wait fix
        # preserves the test's intent. Ranked below the checks above because they are
        # observations while this is a model judgment, and below a missing diff because a run
        # with no DOM change cannot support a claim about product behaviour.
        reason = RefusalReason.LIKELY_PRODUCT_REGRESSION
    else:
        reason = RefusalReason.LOOP_CAP_REACHED

    logger.info("repair_refused", reason=reason.value, loop_count=state["loop_count"])
    return {
        "refusal_reason": reason,
        "evidence_history": add_loop_event(
            state, "refusal_finalizer", "refused", reason=reason.value
        ),
    }


def declined_candidate(state: AgentState) -> bool:
    """Did the model answer, and answer with no change at all?

    Only a successful provider call counts: a failed one leaves ``patch_instructions`` empty
    too, and that is an outage to report as ``provider_error``, not a decline to interpret.
    """
    if not state.get("patch_provider_report", {}).get("ok", True):
        return False
    instructions = state.get("patch_instructions", {})
    return isinstance(instructions, dict) and instructions.get("instructions") == []


def route_after_patch(state: AgentState) -> str:
    """End on a permanent boundary violation; retry a stale line target before verification.

    A candidate that proposes no change leaves the test byte-identical, so it is adjudicated
    before anything is re-verified: re-running the unchanged test cannot answer the question
    the model just raised.
    """
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
    if declined_candidate(state):
        return _DECLINE_ADJUDICATOR
    return "shadow_verifier"


def route_after_decline(state: AgentState) -> str:
    """Refuse a judged behaviour change; otherwise verify the unchanged candidate as before."""
    if state.get("diagnosis_verdict") is DiagnosisVerdict.BEHAVIOUR_CHANGE:
        return _REFUSAL_FINALIZER
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


def _with_next_step(
    state: AgentState, update: dict, node: str
) -> list[EvidenceCandidateRecord] | None:
    """Name ``node`` as the next step of every candidate decided before it ran.

    A candidate is decided by the step that accepts or rejects it, so the node running now is
    where the router sent the run from there. Returns None when nothing changed.
    """
    before = state.get("evidence_candidates", [])
    pending = [
        index
        for index, candidate in enumerate(before)
        if candidate["outcome"] in _DECIDED and "next_step" not in candidate
    ]
    if not pending:
        return None
    after = [
        cast(EvidenceCandidateRecord, dict(candidate))
        for candidate in update.get("evidence_candidates", before)
    ]
    for index in pending:
        after[index]["next_step"] = node
    return after


def _recorded(name: str, node: Node) -> Node:
    """Wrap a node so the run keeps an exact, ordered record of what executed.

    Routing stays in the conditional edges; this only observes it, at the moment it happens,
    so the evidence never re-derives a decision the router made.
    """

    def run(state: AgentState) -> dict:
        update = dict(node(state))
        loop_count = update.get("loop_count", state["loop_count"])
        step: GraphStepRecord = {"node": name, "loop_count": loop_count}
        update["graph_path"] = [*state.get("graph_path", []), step]
        candidates = _with_next_step(state, update, name)
        if candidates is not None:
            update["evidence_candidates"] = candidates
        logger.info("graph_step", node=name, loop_count=loop_count)
        return update

    return run


def build_graph() -> CompiledStateGraph:
    """Build and compile the Diagnoser → Patch Generator → Shadow Verifier → Selector Verifier → Test Runner loop."""
    graph = StateGraph(AgentState)
    nodes: dict[str, Node] = {
        "memory_lookup": memory_lookup,
        "diagnoser": diagnoser,
        "patch_generator": patch_generator,
        _DECLINE_ADJUDICATOR: decline_adjudicator,
        "shadow_verifier": shadow_verifier,
        "selector_verifier": selector_verifier,
        "test_runner": test_runner,
        _REFUSAL_FINALIZER: refusal_finalizer,
    }
    for name, node in nodes.items():
        graph.add_node(name, _recorded(name, node))

    graph.add_conditional_edges(
        START,
        route_at_start,
        {"memory_lookup": "memory_lookup", _REFUSAL_FINALIZER: _REFUSAL_FINALIZER},
    )
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
            _DECLINE_ADJUDICATOR: _DECLINE_ADJUDICATOR,
            _REFUSAL_FINALIZER: _REFUSAL_FINALIZER,
        },
    )
    graph.add_conditional_edges(
        _DECLINE_ADJUDICATOR,
        route_after_decline,
        {"shadow_verifier": "shadow_verifier", _REFUSAL_FINALIZER: _REFUSAL_FINALIZER},
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
