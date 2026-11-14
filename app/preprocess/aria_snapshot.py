"""Extract Playwright's failure-time page (ARIA) snapshot from ``error-context.md``.

On failure, recent Playwright writes ``test-results/<name>/error-context.md`` containing a
``# Page snapshot`` section — a YAML accessibility tree of the page **at the moment of
failure** (after navigation/interaction). It is a deep-state view of the page with no test
modification and no trace parsing.
"""

import re
from pathlib import Path, PurePosixPath

import structlog

from app.sandbox import SandboxViolation, assert_read_allowed

logger = structlog.get_logger(__name__)

# Keep ARIA snapshots bounded for LLM context — raw trees can be very large.
DEFAULT_MAX_SNAPSHOT_CHARS = 2500

_SNAPSHOT_RE = re.compile(r"#\s*Page snapshot\s*```\s*ya?ml\s*\n(.*?)\n```", re.DOTALL)

_LOCATION_RE = re.compile(r"^-\s*Location:\s*(?P<path>.+?)(?::\d+:\d+)?\s*$", re.MULTILINE)
_NAME_RE = re.compile(r"^-\s*Name:\s*(?P<path>[^>\r\n]+?)(?:\s*>>.*)?$", re.MULTILINE)


def _location_matches_test_path(location: str, test_path: Path) -> bool:
    """Return whether a Playwright location identifies ``test_path``."""
    location_parts = tuple(
        part
        for part in PurePosixPath(location.strip().replace("\\", "/")).parts
        if part not in {"/", "."}
    )
    test_parts = tuple(
        part for part in PurePosixPath(test_path.as_posix()).parts if part not in {"/", "."}
    )
    if not location_parts or not test_parts:
        return False
    if len(location_parts) < len(test_parts):
        return test_parts[-len(location_parts) :] == location_parts
    return location_parts[-len(test_parts) :] == test_parts


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

    Returns (matching_paths, diagnostic_message). With no ``test_path``, returns all contexts.
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
        return sorted(contexts, key=lambda p: p.stat().st_mtime, reverse=True), None

    # Playwright records the untruncated test path in the context body. Prefer
    # this identity over the result-directory name, which may be truncated.
    contexts_with_locations: list[tuple[Path, str]] = []
    for path in contexts:
        try:
            content = path.read_text()
        except (FileNotFoundError, OSError) as exc:
            logger.warning("failure_snapshot_identity_read_failed", path=str(path), error=str(exc))
            continue
        location_match = _LOCATION_RE.search(content) or _NAME_RE.search(content)
        if location_match:
            contexts_with_locations.append((path, location_match.group("path")))

    if contexts_with_locations:
        matching = [
            path
            for path, location in contexts_with_locations
            if _location_matches_test_path(location, test_path)
        ]
    else:
        # Older Playwright artifacts carry no Test info: match on the directory name.
        test_stem = test_path.stem  # e.g., "spec" from "scenarios/id-rename/spec.ts"
        test_name = test_path.name  # e.g., "spec.ts"
        matching = [p for p in contexts if test_stem in p.parent.name or test_name in str(p.parent)]

    if not matching:
        logger.warning(
            "failure_snapshot_no_match",
            test_path=str(test_path),
            test_stem=test_path.stem,
            available_contexts=len(contexts),
        )
        return [], f"No error-context.md found for test '{test_path}'"

    if len(matching) > 1:
        # Concurrent runs or stale artifacts can match several; newest wins.
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

    Returns '' when there is no results dir, no matching snapshot, or the file read fails, so
    callers degrade gracefully.
    """
    return read_failure_snapshot_with_source(results_dir, test_path)[0]


def read_failure_snapshot_with_source(
    results_dir: Path, test_path: Path | None = None
) -> tuple[str, Path | None]:
    """Return a failure snapshot and its matching error-context.md path when available."""
    if not results_dir.exists():
        return "", None
    try:
        assert_read_allowed(results_dir)
    except SandboxViolation as exc:
        logger.warning("failure_snapshot_sandbox_denied", path=str(results_dir), error=str(exc))
        return "", None
    contexts, diagnostic = _find_matching_contexts(results_dir, test_path)
    if not contexts:
        if diagnostic:
            logger.info("failure_snapshot_skipped", reason=diagnostic)
        return "", None
    source = contexts[0]
    try:
        content = source.read_text()
    except (FileNotFoundError, OSError) as exc:
        logger.warning("failure_snapshot_read_failed", path=str(source), error=str(exc))
        return "", None
    snapshot = extract_page_snapshot(content)
    logger.info(
        "failure_snapshot_read",
        chars=len(snapshot),
        source=str(source),
        test_path=str(test_path) if test_path else None,
    )
    return (snapshot, source) if snapshot else ("", None)
