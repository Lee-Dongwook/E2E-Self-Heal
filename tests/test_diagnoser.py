from unittest.mock import patch

from app.nodes.diagnoser import DIAGNOSIS_UNAVAILABLE, diagnoser
from app.preprocess.aria_snapshot import abstract_snapshot
from app.state import AgentState
from tests.factories import make_state


def _state(**overrides: object) -> AgentState:
    defaults = {"current_code": 'await page.click("#old")', "error_log": "Error: timeout"}
    return make_state(**(defaults | overrides))


def test_abstract_snapshot_truncates_long_input():
    long_yaml = "x" * 3000
    result = abstract_snapshot(long_yaml, max_chars=100)
    assert len(result) < len(long_yaml)
    assert "[truncated to 100 chars]" in result


def test_diagnoser_includes_snapshot_when_present():
    snapshot = "- role: button\n  name: Submit"
    captured: dict[str, str] = {}

    def fake_generate(system: str, user: str) -> str:
        captured["user"] = user
        return "diagnosis"

    with patch("app.nodes.diagnoser.generate_diagnosis", side_effect=fake_generate):
        diagnoser(_state(dom_snapshot=snapshot))

    assert "ARIA page snapshot" in captured["user"]
    assert "role: button" in captured["user"]


def test_diagnoser_omits_snapshot_section_when_empty():
    captured: dict[str, str] = {}

    def fake_generate(system: str, user: str) -> str:
        captured["user"] = user
        return "diagnosis"

    with patch("app.nodes.diagnoser.generate_diagnosis", side_effect=fake_generate):
        diagnoser(_state(dom_snapshot=""))

    assert "ARIA page snapshot" not in captured["user"]


def test_diagnoser_degrades_on_provider_error():
    def boom(system: str, user: str) -> str:
        raise RuntimeError("provider exhausted retries")

    with patch("app.nodes.diagnoser.generate_diagnosis", side_effect=boom):
        result = diagnoser(_state())

    assert result["analysis_report"] == DIAGNOSIS_UNAVAILABLE
    # The evidence was prepared for the provider call even though it failed, so the counters
    # are still reported rather than dropped.
    assert result["redaction_count"] == 0
    assert result["redaction_applied"] is False
