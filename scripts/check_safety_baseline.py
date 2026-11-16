"""Check that a safety-benchmark report can become ``benchmarks/safety-baseline.json`` (#461).

Every later CI report is compared with the baseline, so a baseline taken from a bad or partial
run makes every comparison after it misleading. A report qualifies only when:

* it is a single pass (``repeats`` 1), the shape of every CI run it will be compared with;
* every release gate passes, none merely ``insufficient_sample``, and there are no errors;
* every scored scenario ended as labeled;
* it covers exactly the checked-in corpus, with the same labels: a baseline from an older corpus
  would leave new scenarios without a comparison.

    uv run --frozen python scripts/check_safety_baseline.py REPORT \\
        --scenario-root examples/scenarios [--previous benchmarks/safety-baseline.json] \\
        [--body pr-body.md]

Exit 0 when the report qualifies, 1 otherwise, listing every reason. ``--body`` also writes a
Markdown summary for the pull request that proposes it, with the changes against ``--previous``.
"""

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from app.safety_benchmark import (
    ActualOutcome,
    GateStatus,
    SafetyBenchmarkReport,
    discover_safety_scenarios,
)
from app.safety_report import load_baseline, render_markdown


def baseline_problems(report: SafetyBenchmarkReport, scenario_root: Path) -> list[str]:
    """Every reason ``report`` can't be a baseline for the corpus under ``scenario_root``."""
    problems: list[str] = []
    if report.repeats != 1:
        problems.append(f"repeats is {report.repeats}; a baseline must be a single pass")
    for gate in report.gates:
        if gate.status is not GateStatus.PASS:
            problems.append(f"gate {gate.name} is {gate.status.value}")
    for result in report.results:
        if result.actual_outcome is ActualOutcome.SKIPPED:
            continue
        if result.actual_outcome.value != result.expected_outcome.value:
            problems.append(
                f"{result.name} ended as {result.actual_outcome.value}, "
                f"labeled {result.expected_outcome.value}"
            )
    corpus = {scenario.name: scenario for scenario in discover_safety_scenarios(scenario_root)}
    reported = {result.name: result for result in report.results}
    for name in sorted(corpus.keys() - reported.keys()):
        problems.append(f"{name} is in the corpus but not in the report")
    for name in sorted(reported.keys() - corpus.keys()):
        problems.append(f"{name} is in the report but not in the corpus")
    for name in sorted(corpus.keys() & reported.keys()):
        scenario, result = corpus[name], reported[name]
        labeled = (scenario.scenario_class, scenario.expected_outcome)
        if (result.scenario_class, result.expected_outcome) != labeled:
            problems.append(f"{name} has different labels in the report than in its meta.json")
        skipped = result.actual_outcome is ActualOutcome.SKIPPED
        if skipped == scenario.runnable:
            state = "skipped although runnable" if skipped else "scored although not runnable"
            problems.append(f"{name} was {state}")
    return problems


def pull_request_body(report: SafetyBenchmarkReport, previous: SafetyBenchmarkReport | None) -> str:
    """The proposing pull request's description: what qualified, then the full comparison."""
    gates = ", ".join(f"`{gate.name}` {gate.status.value}" for gate in report.gates)
    scored = sum(r.actual_outcome is not ActualOutcome.SKIPPED for r in report.results)
    return (
        f"Proposed by the safety-benchmark workflow. All {scored} scored scenarios ended as "
        f"labeled, and every gate passes ({gates}). `scripts/check_safety_baseline.py` checked "
        "the report against the checked-in corpus.\n\n"
        "The comparison below is against the current baseline: merging this resets it.\n\n"
        + render_markdown(report, previous)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a safety report can be the baseline.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--scenario-root", type=Path, required=True)
    parser.add_argument("--previous", type=Path, help="the current baseline, for the PR body")
    parser.add_argument("--body", type=Path, help="write the pull request description here")
    args = parser.parse_args(argv)
    try:
        report = SafetyBenchmarkReport.model_validate_json(args.report.read_text())
    except (OSError, ValidationError) as exc:
        print(f"{args.report} is not a readable safety report: {exc}", file=sys.stderr)
        return 1
    problems = baseline_problems(report, args.scenario_root)
    if problems:
        print(f"{args.report} cannot be the baseline:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if args.body is not None:
        previous = load_baseline(args.previous) if args.previous is not None else None
        args.body.write_text(pull_request_body(report, previous))
    print(f"{args.report} qualifies as the baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
