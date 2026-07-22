"""Tests for --selector-hint CLI flag (Issue #119)."""

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from app.cli import app
from app.schemas import SelectorHint


@pytest.fixture
def mock_heal_dependencies(monkeypatch, tmp_path):
    """Mock all external dependencies so we only test CLI flag parsing."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "playwright.config.ts").write_text("export default {}")
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")

    with (
        patch("app.cli.run_playwright", return_value=(False, "error")),
        patch("app.cli._read_diff", return_value=""),
        patch("app.cli.analyze_diff", return_value=[]),
        patch("app.cli._heal_file") as mock_heal,
    ):
        from app.schemas import RepairSummary

        mock_heal.return_value = RepairSummary(
            test_script_path=str(test_file),
            is_success=True,
            loop_count=0,
            instructions=[],
        )
        yield mock_heal


def test_selector_hint_parsed_and_injected(mock_heal_dependencies, tmp_path):
    """--selector-hint should be parsed and passed through to _heal_file."""
    hint = {
        "type": "role",
        "value": 'button[name="Submit"]',
        "original": "#old-submit-btn",
        "confidence": 0.95,
    }
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["heal", str(tmp_path / "test.spec.ts"), "--selector-hint", json.dumps(hint)],
    )
    assert result.exit_code == 0

    # Verify _heal_file was called with the correct dom_diff_context
    mock_heal_dependencies.assert_called_once()
    call_args = mock_heal_dependencies.call_args
    dom_diff_context = call_args.args[2]  # third positional arg

    # Find the injected hint entry
    hint_entry = next(
        (entry for entry in dom_diff_context if entry.get("type") == "selector_hint"),
        None,
    )
    assert hint_entry is not None, "selector_hint entry not found in dom_diff_context"
    assert hint_entry["hint_type"] == "role"
    assert hint_entry["value"] == 'button[name="Submit"]'
    assert hint_entry["original"] == "#old-submit-btn"
    assert hint_entry["confidence"] == 0.95
    assert hint_entry["priority"] == "high"


def test_selector_hint_invalid_json_exits_2(mock_heal_dependencies, tmp_path):
    """Invalid JSON should exit with code 2 and print an error."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["heal", str(tmp_path / "test.spec.ts"), "--selector-hint", "{invalid json}"],
    )
    assert result.exit_code == 2
    assert "Invalid --selector-hint JSON" in result.stderr


def test_selector_hint_model_validation():
    """SelectorHint model should validate fields correctly."""
    valid = SelectorHint(type="testid", value="submit-btn", original="#old", confidence=0.8)
    assert valid.type == "testid"
    assert valid.confidence == 0.8

    # Use pyright: ignore to allow an invalid literal to reach runtime validation
    with pytest.raises(Exception):
        SelectorHint(type="invalid_type", value="x", original="y")  # pyright: ignore[reportArgumentType]

    with pytest.raises(Exception):
        SelectorHint(type="css", value="x", original="y", confidence=1.5)
