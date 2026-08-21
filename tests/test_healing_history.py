import pytest

import app.healing_history as history_module
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.healing_history import (
    HealingHistoryRecord,
    append_record,
    extract_failing_selector,
    find_match,
    history_path,
    load_history,
    normalize_error_signature,
)
from app.registry import classify_selector_kind
from app.schemas import PatchInstruction


def _record(replacement: str = "#new") -> HealingHistoryRecord:
    error = "Error: waiting for locator('#old') timed out after 30000ms"
    return HealingHistoryRecord(
        normalized_error_signature=normalize_error_signature(error),
        selector_kind=classify_selector_kind("#old"),
        original_selector="#old",
        replacement_selector=replacement,
        instruction=PatchInstruction(
            line=1,
            original="await page.click('#old')",
            replacement=f"await page.click('{replacement}')",
            reason="selector renamed",
            selector=replacement,
        ),
        test_script_path="tests/login.spec.ts",
        provider="test",
        model="test",
        source="llm",
        recorded_at=datetime.now(UTC),
    )


def test_extract_and_normalize_error_signature() -> None:
    error = "Error: waiting for locator('#old') timed out after 30000ms"

    assert extract_failing_selector(error) == "#old"
    assert normalize_error_signature(error) == (
        "error: waiting for locator(<value>) timed out after <n>ms"
    )


def test_append_load_and_deduplicate_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    record = _record()

    assert append_record(record) is True
    assert append_record(record) is False
    assert history_path().is_file()
    assert load_history().records == [record]


def test_matching_requires_exact_selector_and_unambiguous_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    first = _record("#new")
    second = _record("#alternative")
    assert append_record(first) is True
    assert append_record(second) is True

    match, score = find_match(
        "Error: waiting for locator('#old') timed out after 10ms", "x.spec.ts"
    )
    assert match is None
    assert score >= 0.95

    match, score = find_match(
        "Error: waiting for locator('#other') timed out after 10ms", "x.spec.ts"
    )
    assert match is None
    assert score == 0.0


def test_append_holds_an_exclusive_process_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    operations: list[int] = []
    flock = history_module.fcntl.flock

    def tracking_flock(descriptor: int, operation: int) -> None:
        operations.append(operation)
        flock(descriptor, operation)

    monkeypatch.setattr(history_module.fcntl, "flock", tracking_flock)

    assert append_record(_record()) is True
    assert operations == [history_module.fcntl.LOCK_EX, history_module.fcntl.LOCK_UN]
