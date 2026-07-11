"""Shared LangGraph state for the repair loop.

The state is a plain ``TypedDict`` so it stays immutable/traceable across nodes:
each node reads from it and returns a partial update dict.
"""

from typing import NotRequired, TypedDict


class AgentState(TypedDict):
    test_script_path: str  # path to the test file under repair
    original_code: str  # the original test script
    current_code: str  # test script as modified in the current loop
    error_log: str  # latest Playwright error log (abstracted)
    dom_diff_context: list[dict]  # DOM changes from AST parsing
    dom_snapshot: str  # ARIA snapshot of the failing page (from error-context.md)
    analysis_report: str  # Diagnoser's failure-cause report
    patch_instructions: dict  # Patch Generator's fix guide (line, code)
    verification_report: dict  # Selector Verifier's live-DOM match result
    review_report: NotRequired[dict]  # Reviewer's source-level suggestions (review mode only)
    loop_count: int  # infinite-loop guard (max: settings.max_loops)
    is_success: bool  # whether the test passed
