"""Shared LangGraph state for the repair loop.

The state is a plain ``TypedDict`` so it stays immutable/traceable across nodes:
each node reads from it and returns a partial update dict.
"""

from typing import Literal, NotRequired, TypedDict


class PatchApplicationReport(TypedDict):
    """Result of validating generated patch instructions against current code."""

    ok: bool
    error: NotRequired[str]


class MemoryReport(TypedDict):
    """Outcome of local healing-history lookup and candidate verification."""

    attempted: NotRequired[bool]
    hit: NotRequired[bool]
    active: NotRequired[bool]
    score: NotRequired[float]
    source: NotRequired[Literal["llm", "memory"]]
    rejection: NotRequired[str]


class AgentState(TypedDict):
    test_script_path: str  # path to the test file under repair
    original_code: str  # the original test script
    current_code: str  # test script as modified in the current loop
    error_log: str  # latest Playwright error log (abstracted)
    dom_diff_context: list[dict]  # DOM changes from AST parsing
    dom_snapshot: str  # ARIA snapshot of the failing page (from error-context.md)
    analysis_report: str  # Diagnoser's failure-cause report
    detected_framework: NotRequired[str]  # optional framework hint for prompt strategy selection
    patch_instructions: dict  # Patch Generator's fix guide (line, code)
    verification_report: dict  # Selector Verifier's live-DOM match result
    boundary_report: NotRequired[dict]  # Architecture-boundary validation result
    patch_application_report: NotRequired[PatchApplicationReport]
    shadow_report: NotRequired[dict]  # Shadow Verifier's network replay result
    memory_report: NotRequired[MemoryReport]
    review_report: NotRequired[dict]  # Reviewer's source-level suggestions (review mode only)
    loop_count: int  # infinite-loop guard (max: settings.max_loops)
    is_success: bool  # whether the test passed
