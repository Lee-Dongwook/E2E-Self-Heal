from unittest.mock import patch

from app.nodes.diagnoser import diagnoser
from app.preprocess.aria_snapshot import abstract_snapshot
from app.state import AgentState


def _state(**overrides) -> AgentState:
    base: AgentState = {
        "test_script_path": "t.spec.ts",
        "original_code": "",
        "current_code": 'await page.click("#old")',
        "error_log": "Error: timeout",
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
