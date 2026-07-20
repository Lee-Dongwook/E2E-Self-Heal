"""Tests for the memory lookup node."""

from pathlib import Path

import pytest

from app.memory.node import _extract_broken_selector, _extract_error_signature, memory_lookup, memory_save
from app.memory.schemas import HealingRecord, MemoryReport
from app.memory.store import JsonlHealingHistoryStore
from app.schemas import PatchInstruction
from app.state import AgentState


class TestExtractBrokenSelector:
    def test_extracts_locator(self):
        log = 'Timeout waiting for locator("#submit")'
        assert _extract_broken_selector(log) == 'locator("#submit")'

    def test_extracts_id(self):
        log = "Error: #login-btn not found"
        assert _extract_broken_selector(log) == "#login-btn"

    def test_returns_empty_when_no_selector(self):
        assert _extract_broken_selector("random error text") == ""


class TestExtractErrorSignature:
    def test_prefers_error_line(self):
        log = "Some context\nError: Timeout\nMore context"
        assert _extract_error_signature(log) == "Error: Timeout"

    def test_falls_back_to_first_line(self):
        log = "Some context\nMore context"
        assert _extract_error_signature(log) == "Some context"


class TestMemoryLookup:
    def _state(self, **overrides) -> AgentState:
        base: AgentState = {
            "test_script_path": "tests/login.spec.ts",
            "original_code": "await page.locator('#old').click()\n",
            "current_code": "await page.locator('#old').click()\n",
            "error_log": "Timeout waiting for locator('#old')",
            "dom_diff_context": [],
            "dom_snapshot": "",
            "analysis_report": "",
            "patch_instructions": {},
            "verification_report": {},
            "loop_count": 0,
            "is_success": False,
        }
        base.update(overrides)  # type: ignore[typeddict-item]
        return base

    def test_hit_returns_patched_code(self, monkeypatch, tmp_path):
        store = JsonlHealingHistoryStore(tmp_path / "history.jsonl")
        store.save(
            HealingRecord(
                test_script_path="t.spec.ts",
                error_signature="Timeout waiting for locator('#old')",
                broken_selector="locator('#old')",
                fixed_selector="locator('#new')",
                patch_instructions=[
                    {
                        "line": 1,
                        "original": "await page.locator('#old').click()",
                        "replacement": "await page.locator('#new').click()",
                        "reason": "selector renamed",
                        "selector": "#new",
                    }
                ],
            )
        )
        monkeypatch.setattr(
            "app.memory.node.get_default_store", lambda path=None: store
        )

        result = memory_lookup(self._state())

        assert result["memory_report"]["hit"] is True
        assert result["memory_report"]["confidence"] > 0
        assert result["current_code"] == "await page.locator('#new').click()\n"
        assert result["patch_instructions"]["from_memory"] is True

    def test_miss_returns_unchanged(self, monkeypatch, tmp_path):
        store = JsonlHealingHistoryStore(tmp_path / "history.jsonl")
        monkeypatch.setattr(
            "app.memory.node.get_default_store", lambda path=None: store
        )

        result = memory_lookup(self._state())

        assert result["memory_report"]["hit"] is False
        assert "current_code" not in result  # code untouched

    def test_disabled_returns_skip(self, monkeypatch):
        monkeypatch.setattr("app.memory.node.settings.memory_enabled", False)

        result = memory_lookup(self._state())

        assert result["memory_report"]["hit"] is False
        assert result["memory_report"]["error"] == "memory disabled"


class TestMemorySave:
    def _state(self, **overrides) -> AgentState:
        base: AgentState = {
            "test_script_path": "tests/login.spec.ts",
            "original_code": "",
            "current_code": "",
            "error_log": "Timeout waiting for #old",
            "dom_diff_context": [],
            "dom_snapshot": "",
            "analysis_report": "",
            "patch_instructions": {
                "instructions": [
                    {
                        "line": 1,
                        "original": "await page.locator('#old').click()",
                        "replacement": "await page.locator('#new').click()",
                        "reason": "selector renamed",
                        "selector": "#new",
                    }
                ]
            },
            "verification_report": {},
            "loop_count": 1,
            "is_success": True,
        }
        base.update(overrides)  # type: ignore[typeddict-item]
        return base

    def test_saves_successful_repair(self, monkeypatch, tmp_path):
        store = JsonlHealingHistoryStore(tmp_path / "history.jsonl")
        monkeypatch.setattr(
            "app.memory.node.get_default_store", lambda path=None: store
        )

        memory_save(self._state())

        records = store._all_records()
        assert len(records) == 1
        assert records[0].broken_selector == "await page.locator('#old').click()"

    def test_skips_memory_from_memory(self, monkeypatch, tmp_path):
        store = JsonlHealingHistoryStore(tmp_path / "history.jsonl")
        monkeypatch.setattr(
            "app.memory.node.get_default_store", lambda path=None: store
        )

        state = self._state()
        state["patch_instructions"]["from_memory"] = True

        memory_save(state)

        assert len(store._all_records()) == 0

    def test_skips_when_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.memory.node.settings.memory_enabled", False)
        store = JsonlHealingHistoryStore(tmp_path / "history.jsonl")
        monkeypatch.setattr(
            "app.memory.node.get_default_store", lambda path=None: store
        )

        memory_save(self._state())

        assert len(store._all_records()) == 0
