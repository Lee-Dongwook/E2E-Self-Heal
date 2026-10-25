# AI-Driven E2E Test Self-Healing Engine — Development Guide

This document provides essential guidelines for AI agents working on this project.

## Project Overview

An engine that automatically repairs broken Playwright E2E tests. When a UI change
breaks a test's selectors, the engine diagnoses the failure, generates a targeted patch,
re-runs the test, and loops until the test passes or a retry cap is hit.

**Delivery model: a CLI core with a CI integration wrapper.**

- **CLI core** — the engine itself is a single command-line executable. It receives a
  target test path plus the `git diff` and failure log, runs the repair loop, and writes
  the patched test back. This is the source of truth; everything else calls into it.
- **CI wrapper** — a thin pipeline layer (e.g. a GitHub Action / CI step) that detects a
  failing E2E run, invokes the CLI core, and surfaces the result (commit or PR with the
  patch). The wrapper orchestrates; it must contain no repair logic of its own.

Keep this separation strict: repair logic lives in the CLI core so it stays runnable
locally by a developer _and_ callable from CI without duplication.

The system is organized into four layers:

1. **CLI Interface** — user-facing entry point that launches a repair run (also the
   invocation surface CI calls into).
2. **Data Preprocessor (AST analyzer)** — abstracts raw inputs (error logs, `git diff`)
   into compact, hallucination-resistant context for the LLM.
3. **LangGraph Agent** — stateful judgment + repair loop (Diagnoser → Patch Generator → Test Runner).
4. **Test Runner** — validation tool that executes `npx playwright test` via subprocess.

Built with **LangGraph** (agent orchestration), **OpenAI Structured Outputs** (deterministic
patches), and **Playwright** (test execution).

## Architecture

### Data Preprocessor

Input data is abstracted before it reaches the LLM to optimize context-window usage and
prevent hallucination.

- **Error Log Parser** — from the full Playwright log, extract only the `Error:` keyword,
  the failing line number, and the core failure reason from the top of the stack trace
  (e.g. `locator.click: Timeout 5000ms waiting for selector...`).
- **Diff-JSX AST Analyzer** — parse the JSX/TSX regions of a `git diff` and convert the
  before/after DOM node tree into a lightweight JSON object:

  ```json
  {
    "file": "components/SubmitButton.tsx",
    "previous": {
      "tag": "button",
      "attributes": { "id": "old-id", "className": "btn" }
    },
    "current": {
      "tag": "button",
      "attributes": { "id": "new-id", "className": "btn" }
    }
  }
  ```

### LangGraph State

Shared state is immutable and traceable, defined with `TypedDict`:

```python
from typing import TypedDict

class AgentState(TypedDict):
    test_script_path: str         # path to the test file under repair
    original_code: str            # the original test script
    current_code: str             # test script as modified in the current loop
    error_log: str                # latest Playwright error log
    dom_diff_context: list[dict]  # DOM changes from AST parsing
    analysis_report: str          # Diagnoser's failure-cause report
    patch_instructions: dict      # Patch Generator's fix guide (line, code)
    loop_count: int               # infinite-loop guard (max: 3)
    is_success: bool              # whether the test passed
```

### Nodes & Conditional Edges

Three nodes and one conditional edge control the flow.

- **Diagnoser** — maps the failing selector in `error_log` to the actual DOM change in
  `dom_diff_context` and infers the root cause. Updates `analysis_report`.
- **Patch Generator** — uses **OpenAI Structured Outputs** to return the target line and
  replacement code as strict JSON, preventing arbitrary rewrites. Updates `current_code`
  and records `patch_instructions`.
- **Test Runner** — writes `current_code` to disk and runs `npx playwright test <path>`
  via subprocess. On success sets `is_success = True`; on failure refreshes `error_log`
  and increments `loop_count`.
- **Router (conditional edge)** — routes to `[End]` when `is_success` is `True` **or**
  `loop_count >= 3`; otherwise re-enters `[Diagnoser]`.

## Critical Rules

### Guardrails (non-negotiable)

- **Code Integrity** — the Patch Generator may ONLY fix failing locators (selectors) and
  optimize wait conditions. It must never alter test business logic (assertions, flow).
  Enforce this at BOTH the prompt and the JSON schema level.
- **Non-deterministic output control** — wrap LLM calls in `try/except`. On a JSON parse
  failure, feed the error back into the Patch Generator via an internal exception loop
  rather than crashing the graph.
- **Loop cap** — `loop_count` must never exceed 3. The Router is the single source of
  truth for termination.
- **AST lock** — after the per-line scope gates, `_apply` runs `check_ast_lock` on the whole
  patched file, so both the Patch Generator and the memory lookup are covered. It is
  fail-closed: a parse failure or any change other than locator strings, timeouts, and wait
  names is a `PatchGuardrailViolation`. Retry semantics differ by producer. A **Patch
  Generator** violation is a `PatchApplicationError`, so it is handled by the existing internal
  exception loop (never raised out of the node), is fed back to the Patch Generator, and
  **spends a loop**; it can therefore never be retried past the cap, and a terminal one yields
  `RefusalReason.GUARDRAIL_VIOLATION`. A **memory lookup** (history candidate) violation is
  treated as a cache miss: it is recorded as a rejected candidate, does **not** increment
  `loop_count`, and falls through to the LLM path. That is bounded because the memory lookup
  runs once at the start of the graph. Events: `ast_lock_rejected` (reason, node_kind, line)
  and `ast_lock_passed`.
- **Learning never lowers the refusal bar** — local healing history, and any future learned
  rule or policy setting, may reorder candidates or save a model call. It may never skip,
  relax or replace a check. A history candidate is held to everything a model candidate is:
  the ambiguous-target refusal runs before `memory_lookup`, so a remembered repair cannot
  pre-empt it, and an applied history candidate still goes through the `_apply` scope gates
  and AST lock, shadow replay, the live selector check and the test run. Only a verified pass
  is accepted, and only a passing LLM repair is written back to history. No configuration
  value may disable a refusal check or auto-apply an ambiguous change; the only way past the
  ambiguous-target refusal is a human `--selector-hint`. A PR that adds learning or a policy
  setting must say whether it can lower the refusal bar, and the answer must be no.

### Imports

- **All imports MUST be at the top of the file** — never inside functions or classes.

### Logging

- Use **structlog** for all logging.
- Event names must be **lowercase_with_underscores** (e.g. `"patch_generated"`).
- **NO f-strings in structlog events** — pass variables as kwargs.
- Use `logger.exception()` (not `logger.error()`) inside `except` blocks to keep tracebacks.
- Example: `logger.info("test_run_finished", path=path, is_success=is_success, loop_count=count)`

### Retry

- Use the **tenacity** library for retrying flaky LLM / subprocess calls.
- Configure exponential backoff.
- Example: `@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))`

### Console Output

- Use the **rich** library for all CLI output (progress bars, tables, panels, diffs).

## Code Style

### Python

- Target **Python 3.13**; manage deps with **uv**.
- Use type hints on every function signature.
- Prefer Pydantic models / `TypedDict` over raw dictionaries for structured data.
- Use functional, declarative style; reserve classes for agents and services.
- File naming: lowercase with underscores (e.g. `error_log_parser.py`).
- Use the RORO pattern (Receive an Object, Return an Object).

### Error Handling

- Handle errors at the start of functions with guard clauses / early returns.
- Place the happy path last.

## LangGraph Patterns

- Use `StateGraph` to build the repair workflow; compile to `CompiledStateGraph` for use.
- Define state with `AgentState` (see above).
- Use `Command` / conditional edges to control flow between nodes.
- Keep node functions pure where possible: read from state, return a state update.

## Subprocess (Test Runner)

- Run Playwright via `subprocess` (`npx playwright test <path>`); never block the event loop.
- Capture stdout/stderr and pass the raw log to the Error Log Parser, not to the LLM directly.

## CLI Core & CI Wrapper

- **Single entry point** — the CLI core is the only place the repair loop runs. The CI
  wrapper (and any future surface) must shell out to it, never re-implement the graph.
- **Clean I/O contract** — the CLI takes explicit inputs (test path, diff source, config
  via flags/env) and communicates outcome through **exit codes**: `0` = test fixed,
  non-zero = still failing / gave up after the loop cap. CI branches on the exit code.
- **Machine-readable output** — emit a structured summary (e.g. JSON to stdout or a file)
  describing what changed, so the CI wrapper can build a PR/commit message without parsing
  human text. Keep rich-formatted output on stderr / a separate stream.
- **No side effects in the wrapper** — the wrapper only detects failures, invokes the core,
  and reports results (open PR, post status). All diagnosis/patching stays in the core.
- **Local == CI** — the same command a developer runs locally is what CI runs; do not add
  CI-only code paths inside the core.

## Configuration

- Use environment variables / a settings object for config (e.g. OpenAI API key, retry caps).
- Never hardcode secrets or API keys.

## Commandments for This Project

1. The Patch Generator only fixes selectors and wait conditions — never assertions/logic.
2. Enforce the code-integrity guardrail at both prompt and schema level.
3. `loop_count` never exceeds 3; the Router owns termination.
4. All LLM/subprocess retries use the tenacity library.
5. All logs use structlog with lowercase_underscore event names and no f-strings.
6. All CLI output uses rich formatting.
7. All imports are at the top of the file.
8. Preprocess (parse/abstract) inputs before sending anything to the LLM.
9. All function signatures have type hints; structured data uses Pydantic/TypedDict.
10. JSON parse failures feed back into the Patch Generator — never crash the graph.
11. Repair logic lives in the CLI core only; the CI wrapper orchestrates, never re-implements.
12. The CLI signals outcome via exit codes and machine-readable output for CI to branch on.

## Common Pitfalls to Avoid

- ❌ Letting the Patch Generator rewrite assertions or test flow
- ❌ Sending raw Playwright logs / full `git diff` to the LLM instead of abstracted context
- ❌ Using f-strings in structlog events
- ❌ Adding imports inside functions
- ❌ Missing the `loop_count` guard (risk of infinite loops)
- ❌ Using `logger.error()` instead of `logger.exception()` for exceptions
- ❌ Crashing the graph on a malformed LLM JSON response
- ❌ Hardcoding secrets or API keys
- ❌ Missing type hints on function signatures

## When Making Changes

1. Read the existing implementation first.
2. Check for related patterns in the codebase.
3. Keep consistency with existing style.
4. Add structured logging.
5. Include error handling with early returns.
6. Add type hints and Pydantic/TypedDict models.

## References

- LangGraph: https://langchain-ai.github.io/langgraph/
- OpenAI Structured Outputs: https://platform.openai.com/docs/guides/structured-outputs
- Playwright: https://playwright.dev/
