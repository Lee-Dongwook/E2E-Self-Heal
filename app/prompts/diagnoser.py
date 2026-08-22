"""System prompt for the Diagnoser node."""

import json

from app.preprocess.jsx_chunker import CodeChunk

SYSTEM_PROMPT = (
    "You are an expert Playwright E2E test debugger. You are given a failure log, the "
    "DOM changes from a git diff (before/after tag + attributes), an optional ARIA page "
    "snapshot captured at failure time, and the current test code. Explain concisely WHY "
    "the test broke: identify which selector/locator failed and which DOM attribute change "
    "(id, className, data-testid, role, name) caused it. Use the ARIA snapshot to correlate "
    "the failing selector with what was actually on the page. Output a short diagnosis only "
    "— do NOT write code."
)


def build_user_prompt(
    error_log: str, dom_diff_context: list[dict], aria_snapshot: str, code_context: CodeChunk
) -> str:
    """Build the Diagnoser prompt from the same bounded context used in production."""
    user_prompt = (
        f"Error log:\n{error_log}\n\n"
        f"DOM changes (from git diff):\n{json.dumps(dom_diff_context, indent=2)}\n\n"
    )
    if aria_snapshot:
        user_prompt += f"ARIA page snapshot (at failure):\n{aria_snapshot}\n\n"
    context_kind = "whole-file fallback" if code_context.is_fallback else "semantic JSX chunk"
    return (
        user_prompt + f"Current test code context ({context_kind}, lines "
        f"{code_context.start_line}-{code_context.end_line}):\n{code_context.source}"
    )
