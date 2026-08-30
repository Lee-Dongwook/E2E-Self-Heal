"""Extract Playwright's failure-time page (ARIA) snapshot from ``error-context.md``.

On failure, recent Playwright writes ``test-results/<name>/error-context.md`` containing a
``# Page snapshot`` section — a YAML accessibility tree of the page **at the moment of
failure** (after navigation/interaction). This is a hallucination-resistant, deep-state
view of the page, captured with no test modification and no trace parsing.
"""

import re
from pathlib import Path

import structlog

from app.sandbox import SandboxViolation, assert_read_allowed

logger = structlog.get_logger(__name__)

# Keep ARIA snapshots bounded for LLM context — raw trees can be very large.
DEFAULT_MAX_SNAPSHOT_CHARS = 2500

_SNAPSHOT_RE = re.compile(r"#\s*Page snapshot\s*```\s*ya?ml\s*\n(.*?)\n```", re.DOTALL)


def abstract_snapshot(snapshot: str, max_chars: int = DEFAULT_MAX_SNAPSHOT_CHARS) -> str:
    """Return a trimmed ARIA snapshot suitable for Diagnoser context, or '' if empty."""
    text = snapshot.strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [truncated to {max_chars} chars]"


def extract_page_snapshot(error_context_md: str | None) -> str:
    """Return the ARIA page-snapshot YAML from an error-context.md body, or '' if absent."""
    if not error_context_md:
        return ""
    match = _SNAPSHOT_RE.search(error_context_md)
    return match.group(1).strip() if match else ""


def _find_matching_contexts(
    results_dir: Path, test_path: Path | None
) -> tuple[list[Path], str | None]:
    """Find error-context.md files matching the test identity.

    Returns (matching_paths, diagnostic_message).
    When test_path is None, returns all contexts (legacy behavior).
    When test_path is provided, filters to directories containing the test file stem.
    """
    contexts: list[Path] = []
    for path in results_dir.rglob("*error-context*.md"):
        try:
            assert_read_allowed(path)
        except SandboxViolation as exc:
            logger.warning("failure_snapshot_file_sandbox_denied", path=str(path), error=str(exc))
            continue
        contexts.append(path)

    if test_path is None:
        # Legacy: return all contexts, sorted by mtime
        return sorted(contexts, key=lambda p: p.stat().st_mtime, reverse=True), None

    # Filter by test identity: match directories containing the test file stem
    test_stem = test_path.stem  # e.g., "spec" from "scenarios/id-rename/spec.ts"
    test_name = test_path.name  # e.g., "spec.ts"

    matching = [p for p in contexts if test_stem in p.parent.name or test_name in str(p.parent)]

    if not matching:
        # No match found — could be a new test, wrong results dir, or concurrent run
        logger.warning(
            "failure_snapshot_no_match",
            test_path=str(test_path),
            test_stem=test_stem,
            available_contexts=len(contexts),
        )
        return [], f"No error-context.md found for test '{test_path}'"

    if len(matching) > 1:
        # Multiple matches — ambiguous (concurrent runs or stale artifacts)
        # Pick the newest but log the ambiguity
        matching = sorted(matching, key=lambda p: p.stat().st_mtime, reverse=True)
        logger.warning(
            "failure_snapshot_ambiguous_match",
            test_path=str(test_path),
            matches=len(matching),
            picked=str(matching[0]),
        )

    return matching, None


def read_failure_snapshot(results_dir: Path, test_path: Path | None = None) -> str:
    """Return the ARIA page snapshot for the specified test, or '' if unavailable.

    When ``test_path`` is provided, filters error-context.md files to those matching
    the test identity (by file stem). When None, falls back to legacy behavior
    (newest file by mtime across all results).

    Returns '' when no results dir, no matching snapshot, or file read fails (TOCTOU-safe),
    so callers degrade gracefully.
    """
    if not results_dir.exists():
        return ""

    try:
        assert_read_allowed(results_dir)
    except SandboxViolation as exc:
        logger.warning("failure_snapshot_sandbox_denied", path=str(results_dir), error=str(exc))
        return ""

    contexts, diagnostic = _find_matching_contexts(results_dir, test_path)
    if not contexts:
        if diagnostic:
            logger.info("failure_snapshot_skipped", reason=diagnostic)
        return ""

    # Read the newest matching context (TOCTOU-safe)
    try:
        content = contexts[0].read_text()
    except (FileNotFoundError, OSError) as exc:
        # File deleted between glob and read (TOCTOU) or permission error
        logger.warning(
            "failure_snapshot_read_failed",
            path=str(contexts[0]),
            error=str(exc),
        )
        return ""

    snapshot = extract_page_snapshot(content)
    logger.info(
        "failure_snapshot_read",
        chars=len(snapshot),
        source=str(contexts[0]),
        test_path=str(test_path) if test_path else None,
    )
    return snapshot
