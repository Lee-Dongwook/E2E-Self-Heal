"""Persistent, project-local records for safe first-pass selector repairs."""

import os
import re
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterator, Literal

import structlog
from pydantic import BaseModel, Field

from app.config import settings
from app.registry import SelectorKind, classify_selector_kind
from app.schemas import PatchInstruction
from app.sandbox import assert_read_allowed, assert_write_allowed, workspace_root
from app.utils.files import atomic_write

logger = structlog.get_logger(__name__)
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl module.
    fcntl = None

HISTORY_SCHEMA_VERSION = 1
HISTORY_RELATIVE_PATH = Path(".e2e-healer/healing-history.json")
MATCH_THRESHOLD = 0.95
MAX_HISTORY_BYTES = 1_000_000
MAX_HISTORY_RECORDS = 1_000
_SELECTOR_ERROR = re.compile(
    r"""(?P<selector>
        (?:locator|selector|getByRole|getByText|getByLabel|getByPlaceholder|
        getByAltText|getByTitle|getByTestId)
        \((?:[^()\n]+|\([^()\n]*\))*\)
    )""",
    re.IGNORECASE | re.VERBOSE,
)
_LOCATOR_SELECTOR_ERROR = re.compile(
    r"""(?:locator|selector)\(\s*["'](?P<selector>[^"']+)["']\s*\)""", re.IGNORECASE
)
_QUOTED_SELECTOR = re.compile(r"""(["'])(?:\\.|(?!\1).)*\1""")
_NUMBER = re.compile(r"\d+")


class HealingHistoryRecord(BaseModel):
    """One verified selector repair that may be safely reused within this project."""

    schema_version: Literal[1] = HISTORY_SCHEMA_VERSION
    normalized_error_signature: str = Field(min_length=1)
    selector_kind: SelectorKind
    original_selector: str = Field(min_length=1)
    replacement_selectors: tuple[str, ...] = Field(min_length=1)
    instructions: list[PatchInstruction] = Field(min_length=1)
    test_script_path: str
    provider: str
    model: str
    source: Literal["llm", "memory"]
    verified: Literal[True] = True
    recorded_at: datetime


class HealingHistory(BaseModel):
    """Versioned JSON envelope kept at ``.e2e-healer/healing-history.json``."""

    schema_version: Literal[1] = HISTORY_SCHEMA_VERSION
    records: list[HealingHistoryRecord] = Field(default_factory=list)


def history_path() -> Path:
    """Return the only permitted project-local healing history path."""
    return workspace_root() / HISTORY_RELATIVE_PATH


def extract_failing_selector(error_log: str) -> str:
    """Extract a selector from common Playwright error output, if available."""
    match = _LOCATOR_SELECTOR_ERROR.search(error_log) or _SELECTOR_ERROR.search(error_log)
    return match.group("selector") if match else ""


def normalize_error_signature(error_log: str) -> str:
    """Normalize incidental selector values, whitespace, and counters in an error."""
    selector = extract_failing_selector(error_log)
    normalized = error_log.lower()
    if selector:
        normalized = normalized.replace(selector.lower(), "<selector>")
    normalized = _QUOTED_SELECTOR.sub("<value>", normalized)
    normalized = _NUMBER.sub("<n>", normalized)
    return " ".join(normalized.split())


def load_history() -> HealingHistory:
    """Read validated history, treating malformed or unavailable metadata as empty."""
    path = history_path()
    assert_read_allowed(path)
    if not path.exists():
        return HealingHistory()
    try:
        if path.stat().st_size > MAX_HISTORY_BYTES:
            raise ValueError(f"history exceeds {MAX_HISTORY_BYTES} bytes")
        history = HealingHistory.model_validate_json(path.read_text(encoding="utf-8"))
        if len(history.records) > MAX_HISTORY_RECORDS:
            raise ValueError(f"history exceeds {MAX_HISTORY_RECORDS} records")
        return history
    except (OSError, ValueError) as exc:
        logger.warning("healing_history_unavailable", path=str(path), error=str(exc))
        return HealingHistory()


def _canonical_test_path(test_path: str) -> str:
    """Normalize a test path so equivalent CLI spellings share history safely."""
    resolved = Path(test_path).expanduser().resolve()
    try:
        return resolved.relative_to(workspace_root()).as_posix()
    except ValueError:
        return str(resolved)


def _record_key(record: HealingHistoryRecord) -> tuple[str, str, tuple[str, ...], str]:
    return (
        record.normalized_error_signature,
        record.original_selector,
        record.replacement_selectors,
        record.test_script_path,
    )


@contextmanager
def _history_lock(path: Path) -> Iterator[None]:
    """Hold an advisory process lock over a history update's parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        logger.warning("healing_history_lock_unavailable", platform=sys.platform)
        yield
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def append_record(record: HealingHistoryRecord) -> bool:
    """Append one verified record atomically, returning False when it is already present."""
    path = history_path()
    assert_write_allowed(path, reason="healing_history")
    with _history_lock(path):
        history = load_history()
        if _record_key(record) in {_record_key(existing) for existing in history.records}:
            return False
        if len(history.records) >= MAX_HISTORY_RECORDS:
            logger.warning("healing_history_record_limit_reached", limit=MAX_HISTORY_RECORDS)
            return False
        history.records.append(record)
        history.records.sort(key=lambda item: (_record_key(item), item.recorded_at.isoformat()))
        atomic_write(path, history.model_dump_json(indent=2) + "\n", reason="healing_history")
        return True


def find_match(error_log: str, test_path: str) -> tuple[HealingHistoryRecord | None, float]:
    """Return one high-confidence, unambiguous record for the current failing selector."""
    original_selector = extract_failing_selector(error_log)
    if not original_selector:
        return None, 0.0
    signature = normalize_error_signature(error_log)
    selector_kind = classify_selector_kind(original_selector)
    scored: list[tuple[float, HealingHistoryRecord]] = []
    for record in load_history().records:
        if (
            record.test_script_path != _canonical_test_path(test_path)
            or record.original_selector != original_selector
            or record.selector_kind != selector_kind
        ):
            continue
        score = SequenceMatcher(None, signature, record.normalized_error_signature).ratio()
        if score >= MATCH_THRESHOLD:
            scored.append((score, record))
    if not scored:
        return None, 0.0
    scored.sort(key=lambda item: (-item[0], item[1].recorded_at.isoformat()))
    best_score, best = scored[0]
    ambiguous = [
        record
        for score, record in scored
        if best_score - score <= 0.01 and record.replacement_selectors != best.replacement_selectors
    ]
    if ambiguous:
        return None, best_score
    return best, best_score


def make_record(
    *,
    error_log: str,
    instructions: list[PatchInstruction],
    test_script_path: str,
    source: Literal["llm", "memory"],
) -> HealingHistoryRecord | None:
    """Build a record only for a selector-changing verified repair."""
    original_selector = extract_failing_selector(error_log)
    replacement_selectors = tuple(
        instruction.selector for instruction in instructions if instruction.selector
    )
    if not original_selector or not replacement_selectors:
        return None
    return HealingHistoryRecord(
        normalized_error_signature=normalize_error_signature(error_log),
        selector_kind=classify_selector_kind(original_selector),
        original_selector=original_selector,
        replacement_selectors=replacement_selectors,
        instructions=instructions,
        test_script_path=_canonical_test_path(test_script_path),
        provider=settings.llm_provider,
        model=settings.llm_model,
        source=source,
        recorded_at=datetime.now(UTC),
    )
