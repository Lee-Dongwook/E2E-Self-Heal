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
    make_record,
    normalize_error_signature,
)
from app.preprocess.error_log_parser import parse_error_log
from app.registry import classify_selector_kind
from app.schemas import PatchInstruction


def _record(replacement: str = "#new") -> HealingHistoryRecord:
    error = "Error: waiting for locator('#old') timed out after 30000ms"
    return HealingHistoryRecord(
        normalized_error_signature=normalize_error_signature(error),
        selector_kind=classify_selector_kind("#old"),
        original_selector="#old",
        replacement_selectors=(replacement,),
        instructions=[
            PatchInstruction(
                line=1,
                original="await page.click('#old')",
                replacement=f"await page.click('{replacement}')",
                reason="selector renamed",
                selector=replacement,
            )
        ],
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


def test_extracts_role_text_and_test_id_selector_shapes() -> None:
    assert extract_failing_selector("Locator: getByRole('button', { name: 'Save' })") == (
        "getByRole('button', { name: 'Save' })"
    )
    assert extract_failing_selector("waiting for getByText('Continue')") == "getByText('Continue')"
    assert extract_failing_selector("waiting for getByTestId('submit')") == "getByTestId('submit')"


def test_record_matches_the_same_parsed_playwright_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    raw_log = (
        "Error: locator.click: Timeout 30000ms exceeded.\n"
        "Call log:\n"
        "  - waiting for locator('#old')\n" + "unrelated diagnostic text\n" * 50
    )
    parsed_log = parse_error_log(raw_log)
    instruction = _record().instructions[0]
    record = make_record(
        error_log=parsed_log,
        instructions=[instruction],
        test_script_path=str(tmp_path / "tests" / "login.spec.ts"),
        source="llm",
    )
    assert record is not None
    assert append_record(record) is True

    match, score = find_match(parsed_log, str(tmp_path / "tests" / "login.spec.ts"))
    assert match is not None
    assert score >= 0.95


def test_make_record_retains_all_patch_instructions() -> None:
    first = _record().instructions[0]
    second = first.model_copy(
        update={
            "line": 2,
            "original": "await page.click('#next')",
            "replacement": "await page.click('#continue')",
            "selector": "#continue",
        }
    )

    record = make_record(
        error_log="Error: waiting for locator('#old') timed out",
        instructions=[first, second],
        test_script_path="tests/login.spec.ts",
        source="llm",
    )

    assert record is not None
    assert record.replacement_selectors == ("#new", "#continue")
    assert record.instructions == [first, second]


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
        "Error: waiting for locator('#old') timed out after 10ms", "tests/login.spec.ts"
    )
    assert match is None
    assert score == 0.0

    match, score = find_match(
        "Error: waiting for locator('#other') timed out after 10ms", "x.spec.ts"
    )
    assert match is None
    assert score == 0.0


def test_matching_requires_the_same_test_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    assert append_record(_record()) is True
    error = "Error: waiting for locator('#old') timed out after 10ms"

    match, score = find_match(error, str(tmp_path / "tests" / "login.spec.ts"))
    assert match is not None
    assert score >= 0.95

    match, score = find_match(error, str(tmp_path / "tests" / "checkout.spec.ts"))
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


def test_history_append_uses_best_effort_locking_without_fcntl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    monkeypatch.setattr(history_module, "fcntl", None)

    assert append_record(_record()) is True
    assert [record.original_selector for record in load_history().records] == ["#old"]


def test_load_history_rejects_oversized_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    path = history_path()
    path.parent.mkdir()
    path.write_text("x" * (history_module.MAX_HISTORY_BYTES + 1), encoding="utf-8")

    assert load_history().records == []


def test_append_rejects_records_over_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_mode", "strict")
    monkeypatch.setattr(history_module, "MAX_HISTORY_RECORDS", 1)

    assert append_record(_record("#first")) is True
    assert append_record(_record("#second")) is False
