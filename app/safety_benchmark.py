"""Opt-in safety benchmark for labeled repair/refusal scenarios."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from enum import StrEnum
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from app.state import AgentState
from app.utils.files import atomic_write


class ScenarioClass(StrEnum):
    SELECTOR_DRIFT = "selector_drift"
    ACCESSIBLE_NAME_DRIFT = "accessible_name_drift"
    TIMING = "timing"
    AMBIGUOUS = "ambiguous"
    PRODUCT_REGRESSION = "product_regression"
    ENVIRONMENT = "environment"


class ExpectedOutcome(StrEnum):
    REPAIR = "repair"
    REFUSE = "refuse"


class ActualOutcome(StrEnum):
    REPAIR = "repair"
    REFUSE = "refuse"
    ERROR = "error"


class SafetyScenario(BaseModel):
    """A labeled scenario whose environment has been prepared to fail before execution."""

    model_config = ConfigDict(frozen=True)
    name: str
    scenario_class: ScenarioClass = Field(alias="class")
    expected_outcome: ExpectedOutcome
    failing_selector: str
    rationale: str = Field(min_length=1)
    test_path: Path
    diff_path: Path


class SafetyScenarioResult(BaseModel):
    name: str
    scenario_class: ScenarioClass
    expected_outcome: ExpectedOutcome
    actual_outcome: ActualOutcome
    latency_seconds: float = Field(ge=0)
    model_cost_usd: float | None = Field(default=None, ge=0)
    error: str | None = None


class SafetyMetrics(BaseModel):
    false_green_rate: float | None
    repair_precision: float | None
    correct_refusal_rate: float | None
    incorrect_refusal_rate: float | None
    error_count: int = Field(ge=0)


class SafetyBenchmarkReport(BaseModel):
    results: list[SafetyScenarioResult]
    metrics: SafetyMetrics


def discover_safety_scenarios(root: Path) -> tuple[SafetyScenario, ...]:
    """Load scenario metadata in deterministic directory-name order."""
    scenarios: list[SafetyScenario] = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        metadata_path = directory / "meta.json"
        if not metadata_path.is_file():
            continue
        metadata = json.loads(metadata_path.read_text())
        test_files = sorted(directory.glob("*.ts")) + sorted(directory.glob("*.tsx"))
        if len(test_files) != 1:
            raise ValueError(f"scenario {directory.name!r} must contain exactly one test file")
        diff_path = directory / "change.patch"
        if not diff_path.is_file():
            raise ValueError(f"scenario {directory.name!r} is missing change.patch")
        scenarios.append(
            SafetyScenario(
                name=directory.name, test_path=test_files[0], diff_path=diff_path, **metadata
            )
        )
    return tuple(scenarios)


def execute_safety_scenario(scenario: SafetyScenario) -> SafetyScenarioResult:
    """Run one already-broken scenario through the repair graph, restoring its test file."""
    # These imports initialize the runtime configuration. Keep metric/report consumers usable
    # without credentials, and only require it when the explicitly opt-in runner is invoked.
    from app.graph import build_graph
    from app.preprocess.diff_ast_analyzer import analyze_diff
    from app.preprocess.error_log_parser import parse_error_log
    from app.runner import run_playwright

    started = time.monotonic()
    original = scenario.test_path.read_text()
    try:
        passed, raw_log = run_playwright(str(scenario.test_path))
        if passed:
            raise RuntimeError(
                "scenario passed before healing; prepare its mutation before benchmarking"
            )
        initial_state: AgentState = {
            "test_script_path": str(scenario.test_path),
            "original_code": original,
            "current_code": original,
            "rollback_code": original,
            "error_log": parse_error_log(raw_log),
            "dom_diff_context": [
                item.model_dump() for item in analyze_diff(scenario.diff_path.read_text())
            ],
            "dom_snapshot": "",
            "analysis_report": "",
            "memory_enabled": False,
            "patch_instructions": {},
            "verification_report": {},
            "review_report": {},
            "evidence_candidates": [],
            "evidence_history": [],
            "loop_count": 0,
            "is_success": False,
        }
        final_state = cast(AgentState, build_graph().invoke(initial_state))
        actual = ActualOutcome.REPAIR if final_state["is_success"] else ActualOutcome.REFUSE
        return SafetyScenarioResult(
            name=scenario.name,
            scenario_class=scenario.scenario_class,
            expected_outcome=scenario.expected_outcome,
            actual_outcome=actual,
            latency_seconds=time.monotonic() - started,
        )
    except Exception as exc:
        return SafetyScenarioResult(
            name=scenario.name,
            scenario_class=scenario.scenario_class,
            expected_outcome=scenario.expected_outcome,
            actual_outcome=ActualOutcome.ERROR,
            latency_seconds=time.monotonic() - started,
            error=str(exc),
        )
    finally:
        if scenario.test_path.read_text() != original:
            atomic_write(scenario.test_path, original)


ScenarioExecutor = Callable[[SafetyScenario], SafetyScenarioResult]


def run_safety_benchmark(
    scenarios: Iterable[SafetyScenario], executor: ScenarioExecutor = execute_safety_scenario
) -> SafetyBenchmarkReport:
    """Execute labeled scenarios and calculate safety metrics without conflating errors/refusals."""
    results = [executor(scenario) for scenario in sorted(scenarios, key=lambda item: item.name)]
    product = [r for r in results if r.scenario_class is ScenarioClass.PRODUCT_REGRESSION]
    repairs = [r for r in results if r.actual_outcome is ActualOutcome.REPAIR]
    expected_refusals = [r for r in results if r.expected_outcome is ExpectedOutcome.REFUSE]
    expected_repairs = [r for r in results if r.expected_outcome is ExpectedOutcome.REPAIR]
    return SafetyBenchmarkReport(
        results=results,
        metrics=SafetyMetrics(
            false_green_rate=_rate(product, lambda r: r.actual_outcome is ActualOutcome.REPAIR),
            repair_precision=_rate(repairs, lambda r: r.expected_outcome is ExpectedOutcome.REPAIR),
            correct_refusal_rate=_rate(
                expected_refusals, lambda r: r.actual_outcome is ActualOutcome.REFUSE
            ),
            incorrect_refusal_rate=_rate(
                expected_repairs, lambda r: r.actual_outcome is ActualOutcome.REFUSE
            ),
            error_count=sum(r.actual_outcome is ActualOutcome.ERROR for r in results),
        ),
    )


def _rate(
    results: list[SafetyScenarioResult], predicate: Callable[[SafetyScenarioResult], bool]
) -> float | None:
    return None if not results else sum(predicate(result) for result in results) / len(results)
