"""Tests for memory similarity scoring."""

import pytest

from app.memory.schemas import HealingRecord
from app.memory.similarity import (
    _normalize_error,
    _selector_shape,
    compute_similarity,
    find_best_matches,
)


class TestNormalizeError:
    def test_strips_line_numbers(self):
        assert _normalize_error("Error at (42:15) in foo.ts") == "error at in foo.ts"

    def test_strips_uuids(self):
        assert _normalize_error("Error: abc12345-1234-1234-1234-123456789abc") == "error:"

    def test_lowercases_and_collapses_whitespace(self):
        assert _normalize_error("  Error   TIMEOUT  ") == "error timeout"


class TestSelectorShape:
    def test_id_selector(self):
        assert _selector_shape("#submit-btn") == "id"

    def test_class_selector(self):
        assert _selector_shape(".button-primary") == "class"

    def test_getbyrole(self):
        assert _selector_shape('getByRole("button")') == "getbyrole"

    def test_data_testid(self):
        assert _selector_shape('[data-testid="cta"]') == "data-testid"

    def test_text_engine(self):
        assert _selector_shape('text="Submit"') == "text"


class TestComputeSimilarity:
    def test_exact_match(self):
        record = HealingRecord(
            test_script_path="t.spec.ts",
            error_signature="Timeout waiting for #old",
            broken_selector="#old",
            fixed_selector="#new",
            framework="react",
        )
        score = compute_similarity(
            "Timeout waiting for #old", "#old", "react", record
        )
        assert score == 1.0

    def test_no_match(self):
        record = HealingRecord(
            test_script_path="t.spec.ts",
            error_signature="click intercepted",
            broken_selector=".btn",
            fixed_selector=".button",
            framework="vue",
        )
        score = compute_similarity(
            "Timeout waiting for #old", "#old", "react", record
        )
        assert score < 0.5

    def test_framework_bonus(self):
        record = HealingRecord(
            test_script_path="t.spec.ts",
            error_signature="Timeout waiting for selector",
            broken_selector="#old",
            fixed_selector="#new",
            framework="react",
        )
        score_with_fw = compute_similarity(
            "Timeout waiting for selector", "#old", "react", record
        )
        score_without_fw = compute_similarity(
            "Timeout waiting for selector", "#old", "vue", record
        )
        assert score_with_fw > score_without_fw


class TestFindBestMatches:
    def test_returns_matches_above_threshold(self):
        records = [
            HealingRecord(
                test_script_path="t.spec.ts",
                error_signature="Timeout waiting for #submit",
                broken_selector="#submit",
                fixed_selector="#send",
            ),
            HealingRecord(
                test_script_path="t.spec.ts",
                error_signature="completely different error",
                broken_selector=".irrelevant",
                fixed_selector=".other",
            ),
        ]
        results = find_best_matches(
            "Timeout waiting for #submit", "#submit", "", records, threshold=0.75
        )
        assert len(results) == 1
        assert results[0].record.broken_selector == "#submit"
        assert results[0].confidence >= 0.75

    def test_returns_empty_when_nothing_matches(self):
        records = [
            HealingRecord(
                test_script_path="t.spec.ts",
                error_signature="click intercepted",
                broken_selector=".btn",
                fixed_selector=".button",
            ),
        ]
        results = find_best_matches(
            "Timeout waiting for #old", "#old", "", records, threshold=0.75
        )
        assert len(results) == 0

    def test_respects_top_k(self):
        records = [
            HealingRecord(
                test_script_path="t.spec.ts",
                error_signature=f"Timeout waiting for #{i}",
                broken_selector=f"#{i}",
                fixed_selector=f"#fixed{i}",
            )
            for i in range(5)
        ]
        results = find_best_matches(
            "Timeout waiting for #2", "#2", "", records, threshold=0.0, top_k=2
        )
        assert len(results) == 2
