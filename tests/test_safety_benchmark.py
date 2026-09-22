from pathlib import Path

import pytest

from app.safety_benchmark import (
    ActualOutcome,
    ExpectedOutcome,
    SafetyScenario,
    SafetyScenarioResult,
    ScenarioClass,
    discover_safety_scenarios,
    run_safety_benchmark,
)


def _scenario(
    name: str, scenario_class: ScenarioClass, expected: ExpectedOutcome
) -> SafetyScenario:
    return SafetyScenario(
        name=name,
        **{"class": scenario_class},
        expected_outcome=expected,
        failing_selector="#submit",
        rationale="A labeled scenario.",
        test_path=Path("spec.ts"),
        diff_path=Path("change.patch"),
    )


def test_metrics_keep_errors_separate_from_refusals() -> None:
    scenarios = (
        _scenario("regression", ScenarioClass.PRODUCT_REGRESSION, ExpectedOutcome.REFUSE),
        _scenario("drift", ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR),
        _scenario("ambiguous", ScenarioClass.AMBIGUOUS, ExpectedOutcome.REFUSE),
    )
    outcomes = {
        "regression": ActualOutcome.REPAIR,
        "drift": ActualOutcome.REFUSE,
        "ambiguous": ActualOutcome.ERROR,
    }

    def execute(scenario: SafetyScenario) -> SafetyScenarioResult:
        return SafetyScenarioResult(
            name=scenario.name,
            scenario_class=scenario.scenario_class,
            expected_outcome=scenario.expected_outcome,
            actual_outcome=outcomes[scenario.name],
            latency_seconds=1,
        )

    report = run_safety_benchmark(reversed(scenarios), execute)
    assert [result.name for result in report.results] == ["ambiguous", "drift", "regression"]
    assert report.metrics.false_green_rate == 1.0
    assert report.metrics.repair_precision == 0.0
    assert report.metrics.correct_refusal_rate == 0.0
    assert report.metrics.incorrect_refusal_rate == 1.0
    assert report.metrics.error_count == 1


def test_discovery_rejects_ambiguous_test_inputs(tmp_path: Path) -> None:
    directory = tmp_path / "scenario"
    directory.mkdir()
    (directory / "meta.json").write_text(
        '{"class":"selector_drift","expected_outcome":"repair","failing_selector":"#x","rationale":"ok"}'
    )
    (directory / "change.patch").write_text("")
    (directory / "a.ts").write_text("")
    (directory / "b.ts").write_text("")
    with pytest.raises(ValueError, match="exactly one test file"):
        discover_safety_scenarios(tmp_path)


def test_checked_in_scenarios_are_labeled() -> None:
    scenarios = discover_safety_scenarios(Path("examples/scenarios"))

    assert [scenario.name for scenario in scenarios] == [
        "classname-rename",
        "id-rename",
        "jsx-context",
        "product-regression",
    ]
    assert scenarios[-1].expected_outcome is ExpectedOutcome.REFUSE
