from collections.abc import Iterator

import pytest

import app.nodes.decline_adjudicator as adjudicator_node
from app.nodes.decline_adjudicator import decline_adjudicator
from app.nodes.diagnoser import DIAGNOSIS_UNAVAILABLE
from app.schemas import DiagnosisVerdict, DiagnosisVerdictOutput
from app.state import AgentState


def _state(**overrides) -> AgentState:
    base: AgentState = {
        "test_script_path": "t.spec.ts",
        "original_code": "await page.click('#submit-btn')\n",
        "current_code": "await page.click('#submit-btn')\n",
        "error_log": "Error: Timed out waiting for 'Thanks!'",
        "dom_diff_context": [{"file": "src/App.tsx", "line": 22}],
        "dom_snapshot": "",
        "analysis_report": "The submit handler was removed, so the confirmation never renders.",
        "patch_instructions": {"instructions": []},
        "patch_provider_report": {"ok": True},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _verdict(verdict: DiagnosisVerdict) -> DiagnosisVerdictOutput:
    return DiagnosisVerdictOutput(verdict=verdict, rationale="the handler is gone")


@pytest.fixture
def prompts(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str]]]:
    """Capture the prompt, answering with behaviour_change unless a test overrides it."""
    captured: list[tuple[str, str]] = []

    def fake_generate(system_prompt: str, user_prompt: str) -> DiagnosisVerdictOutput:
        captured.append((system_prompt, user_prompt))
        return _verdict(DiagnosisVerdict.BEHAVIOUR_CHANGE)

    monkeypatch.setattr(adjudicator_node, "generate_diagnosis_verdict", fake_generate)
    yield captured


def test_a_behaviour_change_verdict_is_recorded_and_spends_one_loop(prompts) -> None:
    result = decline_adjudicator(_state(loop_count=1))

    assert result["diagnosis_verdict"] is DiagnosisVerdict.BEHAVIOUR_CHANGE
    # The loop the skipped test run would have spent, so the cap still bounds the work.
    assert result["loop_count"] == 2


@pytest.mark.parametrize("verdict", [DiagnosisVerdict.SELECTOR_DRIFT, DiagnosisVerdict.UNCLEAR])
def test_a_non_terminal_verdict_spends_no_loop(
    monkeypatch: pytest.MonkeyPatch, verdict: DiagnosisVerdict
) -> None:
    monkeypatch.setattr(
        adjudicator_node,
        "generate_diagnosis_verdict",
        lambda _system, _user: _verdict(verdict),
    )

    result = decline_adjudicator(_state(loop_count=1))

    assert result["diagnosis_verdict"] is verdict
    # The unchanged test run still spends its own loop, as before this node existed.
    assert "loop_count" not in result


def test_a_cached_verdict_is_not_asked_again(prompts) -> None:
    # A repeated decline leaves every prompt unchanged, so asking again would only pay twice.
    result = decline_adjudicator(_state(diagnosis_verdict=DiagnosisVerdict.UNCLEAR))

    assert result == {}
    assert prompts == []


def test_a_provider_failure_records_no_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_system: str, _user: str) -> DiagnosisVerdictOutput:
        raise RuntimeError("provider exhausted retries")

    monkeypatch.setattr(adjudicator_node, "generate_diagnosis_verdict", boom)

    result = decline_adjudicator(_state())

    # No verdict: the run keeps the path it takes today instead of claiming a regression.
    assert "diagnosis_verdict" not in result
    assert "loop_count" not in result
    assert result["redaction_count"] == 0


def test_a_degraded_diagnosis_is_not_adjudicated(prompts) -> None:
    result = decline_adjudicator(_state(analysis_report=DIAGNOSIS_UNAVAILABLE))

    assert result == {}
    assert prompts == []


def test_the_prompt_carries_the_diagnosis_and_the_failure_evidence(prompts) -> None:
    decline_adjudicator(_state())

    system_prompt, user_prompt = prompts[0]
    assert "The submit handler was removed" in user_prompt
    assert "Timed out waiting for 'Thanks!'" in user_prompt
    assert "src/App.tsx" in user_prompt
