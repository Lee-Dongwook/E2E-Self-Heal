"""What a report must satisfy to become the safety baseline (#461)."""

from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.check_safety_baseline import baseline_problems, main
from app.safety_benchmark import (
    ActualOutcome,
    ExpectedOutcome,
    SafetyBenchmarkReport,
    SafetyScenarioResult,
    build_report,
    discover_safety_scenarios,
)

CORPUS = Path("examples/scenarios")


def _results() -> list[SafetyScenarioResult]:
    """A clean single pass over the checked-in corpus: every scenario as labeled."""
    return [
        SafetyScenarioResult(
            name=scenario.name,
            scenario_class=scenario.scenario_class,
            expected_outcome=scenario.expected_outcome,
            actual_outcome=(
                ActualOutcome(scenario.expected_outcome.value)
                if scenario.runnable
                else ActualOutcome.SKIPPED
            ),
            latency_seconds=1,
        )
        for scenario in discover_safety_scenarios(CORPUS)
    ]


def _with(name: str, **update: object) -> list[SafetyScenarioResult]:
    return [r.model_copy(update=update) if r.name == name else r for r in _results()]


def test_a_clean_single_pass_over_the_corpus_qualifies() -> None:
    assert baseline_problems(build_report(_results()), CORPUS) == []


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (
            lambda: build_report(_with("id-rename", actual_outcome=ActualOutcome.ERROR)),
            ["gate error_count is fail", "id-rename ended as error, labeled repair"],
        ),
        (
            lambda: build_report(_with("split-submit", actual_outcome=ActualOutcome.REPAIR)),
            # One miss in 23 keeps refusal_accuracy at 95.7%, above its gate: only the label
            # check catches it.
            ["split-submit ended as repair, labeled refuse"],
        ),
        (
            lambda: build_report([r for r in _results() if r.name != "submit-another-added"]),
            ["submit-another-added is in the corpus but not in the report"],
        ),
        (
            lambda: build_report([*_results(), _results()[0].model_copy(update={"name": "gone"})]),
            ["gone is in the report but not in the corpus"],
        ),
        (
            lambda: build_report(
                _with(
                    "id-rename",
                    expected_outcome=ExpectedOutcome.REFUSE,
                    actual_outcome=ActualOutcome.REFUSE,
                )
            ),
            ["id-rename has different labels in the report than in its meta.json"],
        ),
        (
            lambda: build_report(_with("jsx-context", actual_outcome=ActualOutcome.REPAIR)),
            ["jsx-context was scored although not runnable"],
        ),
        (
            lambda: build_report(_results()).model_copy(update={"repeats": 3}),
            ["repeats is 3; a baseline must be a single pass"],
        ),
    ],
    ids=[
        "an-error",
        "a-wrong-outcome",
        "a-missing-scenario",
        "an-extra-scenario",
        "a-relabel",
        "a-skipped-scenario-scored",
        "repeated-runs",
    ],
)
def test_a_report_that_would_mislead_later_comparisons_is_rejected(
    report: Callable[[], SafetyBenchmarkReport], expected: list[str]
) -> None:
    assert baseline_problems(report(), CORPUS) == expected


def test_a_gate_below_its_minimum_sample_does_not_qualify(tmp_path: Path) -> None:
    # A small corpus can't enforce the rate gates, so their status is insufficient_sample.
    scenario = CORPUS / "id-rename"
    corpus = tmp_path / "scenarios"
    (corpus / "id-rename").mkdir(parents=True)
    for item in scenario.iterdir():
        (corpus / "id-rename" / item.name).write_bytes(item.read_bytes())
    result = next(r for r in _results() if r.name == "id-rename")

    problems = baseline_problems(build_report([result]), corpus)

    assert "gate incorrect_refusal_rate is insufficient_sample" in problems


def test_the_command_writes_the_pull_request_body_only_for_a_qualifying_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good, bad, body = tmp_path / "good.json", tmp_path / "bad.json", tmp_path / "body.md"
    good.write_text(build_report(_results()).model_dump_json())
    bad.write_text(
        build_report(_with("id-rename", actual_outcome=ActualOutcome.ERROR)).model_dump_json()
    )

    assert main([str(bad), "--scenario-root", str(CORPUS), "--body", str(body)]) == 1
    assert not body.exists()
    assert "id-rename ended as error" in capsys.readouterr().err

    assert (
        main(
            [str(good), "--scenario-root", str(CORPUS), "--previous", str(bad), "--body", str(body)]
        )
        == 0
    )
    text = body.read_text()
    assert "ended as labeled, and every gate passes" in text
    # The comparison is against --previous: id-rename flips from error to repair.
    assert "error → repair" in text


def test_an_unreadable_report_is_rejected(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text("{}")

    assert main([str(report), "--scenario-root", str(CORPUS)]) == 1
