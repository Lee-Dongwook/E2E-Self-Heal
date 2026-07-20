"""Tests for the JSONL healing-history store."""

from pathlib import Path

import pytest

from app.memory.schemas import HealingRecord
from app.memory.store import JsonlHealingHistoryStore


@pytest.fixture
def store(tmp_path: Path) -> JsonlHealingHistoryStore:
    return JsonlHealingHistoryStore(tmp_path / "history.jsonl")


class TestSave:
    def test_creates_file(self, store: JsonlHealingHistoryStore, tmp_path: Path):
        record = HealingRecord(
            test_script_path="t.spec.ts",
            error_signature="Timeout",
            broken_selector="#old",
            fixed_selector="#new",
        )
        store.save(record)
        assert store._path.exists()

    def test_appends_line(self, store: JsonlHealingHistoryStore):
        record = HealingRecord(
            test_script_path="t.spec.ts",
            error_signature="Timeout",
            broken_selector="#old",
            fixed_selector="#new",
        )
        store.save(record)
        lines = store._path.read_text().strip().splitlines()
        assert len(lines) == 1
        parsed = HealingRecord.model_validate_json(lines[0])
        assert parsed.broken_selector == "#old"

    def test_appends_multiple(self, store: JsonlHealingHistoryStore):
        for i in range(3):
            store.save(
                HealingRecord(
                    test_script_path=f"t{i}.spec.ts",
                    error_signature=f"Error {i}",
                    broken_selector=f"#{i}",
                    fixed_selector=f"#fixed{i}",
                )
            )
        lines = store._path.read_text().strip().splitlines()
        assert len(lines) == 3


class TestQuery:
    def test_returns_match(self, store: JsonlHealingHistoryStore):
        store.save(
            HealingRecord(
                test_script_path="t.spec.ts",
                error_signature="Timeout waiting for #submit",
                broken_selector="#submit",
                fixed_selector="#send",
            )
        )
        results = store.query("Timeout waiting for #submit", "#submit")
        assert len(results) == 1
        assert results[0].record.fixed_selector == "#send"

    def test_returns_empty_on_miss(self, store: JsonlHealingHistoryStore):
        store.save(
            HealingRecord(
                test_script_path="t.spec.ts",
                error_signature="click intercepted",
                broken_selector=".btn",
                fixed_selector=".button",
            )
        )
        results = store.query("Timeout waiting for #old", "#old")
        assert len(results) == 0

    def test_ignores_corrupted_lines(self, store: JsonlHealingHistoryStore):
        store._ensure_file()
        with store._path.open("a") as fh:
            fh.write('{"broken_selector": "#bad"}\n')  # missing required fields
            fh.write("not json at all\n")
        # Should not crash
        results = store.query("anything", "#anything")
        assert len(results) == 0

    def test_exact_match_with_framework_hits_1_0(self, store: JsonlHealingHistoryStore):
        store.save(
            HealingRecord(
                test_script_path="t.spec.ts",
                error_signature="Timeout waiting for #submit",
                broken_selector="#submit",
                fixed_selector="#send",
                framework="react",
            )
        )
        results = store.query(
            "Timeout waiting for #submit", "#submit", framework="react", threshold=1.0
        )
        assert len(results) == 1
        assert results[0].confidence == 1.0

    def test_high_threshold_excludes_partial_match(self, store: JsonlHealingHistoryStore):
        store.save(
            HealingRecord(
                test_script_path="t.spec.ts",
                error_signature="Timeout waiting for #submit",
                broken_selector="#submit",
                fixed_selector="#send",
            )
        )
        # No framework set → max score 0.8, so threshold=1.0 returns nothing
        results = store.query("Timeout waiting for #submit", "#submit", threshold=1.0)
        assert len(results) == 0
