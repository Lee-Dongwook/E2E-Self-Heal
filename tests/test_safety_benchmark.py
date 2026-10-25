import subprocess
from pathlib import Path

import pytest

import app.graph as graph_module
import app.runner as runner_module
from app.config import settings
from app.schemas import RefusalReason
from app.safety_benchmark import (
    GATE_MIN_SAMPLE,
    ActualOutcome,
    ExpectedOutcome,
    GateResult,
    GateStatus,
    SafetyBenchmarkReport,
    SafetyScenario,
    SafetyScenarioResult,
    ScenarioClass,
    ScenarioPatchError,
    ScenarioRestoreError,
    ScenarioSandboxError,
    applied_scenario_patch,
    assert_scenarios_writable,
    build_report,
    discover_safety_scenarios,
    execute_prepared_scenario,
    execute_safety_scenario,
    run_safety_benchmark,
)


def _scenario(
    name: str,
    scenario_class: ScenarioClass,
    expected: ExpectedOutcome,
    *,
    runnable: bool = True,
) -> SafetyScenario:
    return SafetyScenario.model_validate(
        {
            "name": name,
            "class": scenario_class,
            "expected_outcome": expected,
            "failing_selector": "#submit",
            "rationale": "A labeled scenario.",
            "test_path": Path("spec.ts"),
            "diff_path": Path("change.patch"),
            "runnable": runnable,
        }
    )


def _result(
    name: str,
    scenario_class: ScenarioClass,
    expected: ExpectedOutcome,
    actual: ActualOutcome,
    *,
    latency: float = 1,
    attempts: int | None = None,
    cost: float | None = None,
) -> SafetyScenarioResult:
    return SafetyScenarioResult(
        name=name,
        scenario_class=scenario_class,
        expected_outcome=expected,
        actual_outcome=actual,
        latency_seconds=latency,
        attempts=attempts,
        model_cost_usd=cost,
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
        return _result(
            scenario.name,
            scenario.scenario_class,
            scenario.expected_outcome,
            outcomes[scenario.name],
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


def test_checked_in_corpus_covers_every_class_with_consistent_labels() -> None:
    scenarios = discover_safety_scenarios(Path("examples/scenarios"))
    refuse_classes = {
        ScenarioClass.AMBIGUOUS,
        ScenarioClass.PRODUCT_REGRESSION,
        ScenarioClass.ENVIRONMENT,
    }

    assert {s.scenario_class for s in scenarios if s.runnable} == set(ScenarioClass)
    for scenario in scenarios:
        expected = (
            ExpectedOutcome.REFUSE
            if scenario.scenario_class in refuse_classes
            else ExpectedOutcome.REPAIR
        )
        assert scenario.expected_outcome is expected, scenario.name
        assert scenario.failing_selector in scenario.test_path.read_text(), scenario.name


def test_checked_in_runnable_specs_have_no_comments() -> None:
    # The model reads the spec. A comment such as "this is a regression" hands it the label,
    # so the explanation belongs in meta.json's rationale, which the model never sees.
    for scenario in discover_safety_scenarios(Path("examples/scenarios")):
        if scenario.runnable:
            assert "//" not in scenario.test_path.read_text(), scenario.name


def test_static_fixture_is_the_only_checked_in_scenario_that_is_not_runnable() -> None:
    # jsx-context is a token-benchmark fixture, not a Playwright test. If it were scored it
    # would count as a failed repair and skew every rate.
    runnable = {s.name: s.runnable for s in discover_safety_scenarios(Path("examples/scenarios"))}

    assert [name for name, is_runnable in runnable.items() if not is_runnable] == ["jsx-context"]


def test_every_runnable_checked_in_scenario_matches_the_demo_app_in_some_state() -> None:
    # Guards the corpus itself: a patch that neither applies nor is already applied (as
    # classname-rename's once appeared to) would make a CI run report a permanent error.
    root = Path("examples/scenarios")
    for scenario in discover_safety_scenarios(root):
        if not scenario.runnable:
            continue
        patch = str(scenario.diff_path.resolve())
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        forward = subprocess.run(
            ["git", "apply", "--check", patch], cwd=toplevel, capture_output=True, text=True
        )
        reverse = subprocess.run(
            ["git", "apply", "--check", "--reverse", patch],
            cwd=toplevel,
            capture_output=True,
            text=True,
        )
        assert forward.returncode == 0 or reverse.returncode == 0, (
            f"{scenario.name}: change.patch matches the demo app in neither state: "
            f"{forward.stderr.strip()}"
        )


# --- corpus size against the release gates ----------------------------------------------


def _ideal_results(
    scenarios: tuple[SafetyScenario, ...],
) -> list[SafetyScenarioResult]:
    """Results for a run in which every scenario ended as its label says, with no errors."""
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
            latency_seconds=0,
        )
        for scenario in scenarios
    ]


def test_the_checked_in_corpus_is_large_enough_to_enforce_every_rate_gate() -> None:
    # A corpus that cannot enforce the gates makes them decorative. This fails as soon as a
    # scenario is removed, so the corpus cannot quietly shrink.
    scenarios = discover_safety_scenarios(Path("examples/scenarios"))

    report = build_report(_ideal_results(scenarios))

    assert {gate.status for gate in report.gates} == {GateStatus.PASS}, [
        (gate.name, gate.status, gate.sample_size) for gate in report.gates
    ]
    assert {gate.scenarios_needed for gate in report.gates} == {0}


def test_every_drift_scenario_has_a_regression_twin_that_looks_identical_to_the_model() -> None:
    # The gates measure discrimination only if each regression has a drift look-alike: same
    # spec text, same selector, repaired in one case and refused in the other.
    by_name = {
        scenario.name: scenario
        for scenario in discover_safety_scenarios(Path("examples/scenarios"))
    }
    drifts = [scenario for name, scenario in by_name.items() if name.endswith("-drift")]

    assert drifts
    for drift in drifts:
        twin = f"{drift.name.removesuffix('-drift')}-regression"
        assert twin in by_name, f"{drift.name} has no {twin}"
        regression = by_name[twin]
        assert drift.scenario_class is ScenarioClass.SELECTOR_DRIFT, drift.name
        assert drift.expected_outcome is ExpectedOutcome.REPAIR, drift.name
        assert regression.scenario_class is ScenarioClass.PRODUCT_REGRESSION, drift.name
        assert regression.expected_outcome is ExpectedOutcome.REFUSE, drift.name
        assert regression.failing_selector == drift.failing_selector, drift.name
        assert regression.test_path.read_text() == drift.test_path.read_text(), drift.name


def _scenario_at(test_path: Path, *, runnable: bool = True) -> SafetyScenario:
    return SafetyScenario.model_validate(
        {
            "name": "s",
            "class": ScenarioClass.SELECTOR_DRIFT,
            "expected_outcome": ExpectedOutcome.REPAIR,
            "failing_selector": "#x",
            "rationale": "A labeled scenario.",
            "test_path": test_path,
            "diff_path": test_path.parent / "change.patch",
            "runnable": runnable,
        }
    )


def test_a_scenario_file_the_sandbox_forbids_writing_fails_fast_with_guidance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "sandbox_mode", "relaxed")
    monkeypatch.setattr(settings, "write_globs", "*.spec.ts,**/*.spec.ts")

    with pytest.raises(ScenarioSandboxError, match="E2E_HEALER_WRITE_GLOBS") as caught:
        # A file literally named spec.ts does not match `*.spec.ts`.
        assert_scenarios_writable([_scenario_at(tmp_path / "spec.ts")])

    assert "s: " in str(caught.value)


def test_scenario_files_the_sandbox_allows_pass_the_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "sandbox_mode", "relaxed")
    monkeypatch.setattr(settings, "write_globs", "*.spec.ts,**/*.spec.ts,spec.ts,**/spec.ts")

    assert_scenarios_writable(
        [_scenario_at(tmp_path / "spec.ts"), _scenario_at(tmp_path / "login.spec.ts")]
    )


def test_the_preflight_ignores_scenarios_that_are_not_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A static fixture is never edited, so its filename must not matter.
    monkeypatch.setattr(settings, "sandbox_mode", "relaxed")
    monkeypatch.setattr(settings, "write_globs", "*.spec.ts,**/*.spec.ts")

    assert_scenarios_writable([_scenario_at(tmp_path / "fixture.tsx", runnable=False)])


def test_runnable_defaults_to_true_and_is_read_from_metadata(tmp_path: Path) -> None:
    for name, extra in (("default", ""), ("static", ',"runnable":false')):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "meta.json").write_text(
            '{"class":"selector_drift","expected_outcome":"repair",'
            f'"failing_selector":"#x","rationale":"ok"{extra}}}'
        )
        (directory / "change.patch").write_text("")
        (directory / "spec.ts").write_text("")

    by_name = {s.name: s.runnable for s in discover_safety_scenarios(tmp_path)}

    assert by_name == {"default": True, "static": False}


def test_skipped_scenarios_are_reported_but_never_scored_or_executed() -> None:
    scenarios = (
        _scenario("static", ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR, runnable=False),
        _scenario("drift", ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR),
    )
    executed: list[str] = []

    def execute(scenario: SafetyScenario) -> SafetyScenarioResult:
        executed.append(scenario.name)
        return _result(
            scenario.name, scenario.scenario_class, scenario.expected_outcome, ActualOutcome.REPAIR
        )

    report = run_safety_benchmark(scenarios, execute)

    assert executed == ["drift"]
    assert {r.name: r.actual_outcome for r in report.results} == {
        "drift": ActualOutcome.REPAIR,
        "static": ActualOutcome.SKIPPED,
    }
    # The skipped scenario is in no population: it is neither a wrong refusal nor an error.
    assert report.metrics.incorrect_refusal_rate == 0.0
    assert report.metrics.sample_sizes.incorrect_refusal_rate == 1
    assert report.metrics.error_count == 0
    drift = next(s for s in report.per_class if s.scenario_class is ScenarioClass.SELECTOR_DRIFT)
    assert (drift.scenarios, drift.skipped, drift.correct) == (2, 1, 1)


def test_per_class_summary_lists_every_class_including_empty_ones() -> None:
    report = build_report(
        [
            _result(
                "regression",
                ScenarioClass.PRODUCT_REGRESSION,
                ExpectedOutcome.REFUSE,
                ActualOutcome.REPAIR,
                latency=4,
                attempts=2,
            ),
            _result(
                "drift-a",
                ScenarioClass.SELECTOR_DRIFT,
                ExpectedOutcome.REPAIR,
                ActualOutcome.REPAIR,
                latency=2,
                attempts=1,
            ),
            _result(
                "drift-b",
                ScenarioClass.SELECTOR_DRIFT,
                ExpectedOutcome.REPAIR,
                ActualOutcome.ERROR,
                latency=6,
            ),
        ]
    )

    assert [s.scenario_class for s in report.per_class] == list(ScenarioClass)
    by_class = {s.scenario_class: s for s in report.per_class}
    drift = by_class[ScenarioClass.SELECTOR_DRIFT]
    assert (drift.scenarios, drift.repaired, drift.errors, drift.correct) == (2, 1, 1, 1)
    # Errors count toward latency but not toward attempts, which an error never reports.
    assert drift.mean_latency_seconds == 4.0
    assert drift.mean_attempts == 1.0
    regression = by_class[ScenarioClass.PRODUCT_REGRESSION]
    assert (regression.expected_refuse, regression.repaired, regression.correct) == (1, 1, 0)
    # A class with no scenarios is listed with zeros and no averages, so the gap is visible.
    ambiguous = by_class[ScenarioClass.AMBIGUOUS]
    assert (ambiguous.scenarios, ambiguous.correct) == (0, 0)
    assert ambiguous.mean_latency_seconds is None
    assert ambiguous.mean_attempts is None


def test_metric_sample_sizes_expose_the_denominators() -> None:
    report = build_report(
        [
            _result(
                "regression",
                ScenarioClass.PRODUCT_REGRESSION,
                ExpectedOutcome.REFUSE,
                ActualOutcome.REFUSE,
            ),
            _result(
                "drift-a",
                ScenarioClass.SELECTOR_DRIFT,
                ExpectedOutcome.REPAIR,
                ActualOutcome.REPAIR,
            ),
            _result(
                "drift-b",
                ScenarioClass.SELECTOR_DRIFT,
                ExpectedOutcome.REPAIR,
                ActualOutcome.REPAIR,
            ),
        ]
    )

    sizes = report.metrics.sample_sizes
    assert (sizes.false_green_rate, sizes.repair_precision) == (1, 2)
    assert (sizes.correct_refusal_rate, sizes.incorrect_refusal_rate) == (1, 2)
    assert report.metrics.false_green_rate == 0.0


def test_cost_is_unavailable_unless_every_scored_scenario_reports_it() -> None:
    drift = (ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR, ActualOutcome.REPAIR)

    unmeasured = build_report([_result("a", *drift), _result("b", *drift)])
    partial = build_report([_result("a", *drift, cost=0.01), _result("b", *drift)])
    measured = build_report([_result("a", *drift, cost=0.01), _result("b", *drift, cost=0.02)])

    # None means "not measured", never zero: a missing cost must not read as a free run.
    assert unmeasured.total_model_cost_usd is None
    assert partial.total_model_cost_usd is None
    assert measured.total_model_cost_usd == 0.03


# --- executing one scenario through the graph (graph and Playwright stubbed) ---------


_CHANGE = {"instructions": [{"line": 1}]}
_NO_CHANGE = {"instructions": []}


class _StubGraph:
    def __init__(self, final: dict[str, object], on_invoke=None) -> None:
        self._final = final
        self._on_invoke = on_invoke

    def invoke(self, state: dict[str, object]) -> dict[str, object]:
        if self._on_invoke is not None:
            self._on_invoke(state)
        return {**state, **self._final}


@pytest.fixture
def graph_scenario(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SafetyScenario:
    monkeypatch.setattr(settings, "sandbox_mode", "relaxed")
    monkeypatch.setattr(settings, "write_globs", "*.spec.ts,**/*.spec.ts")
    (tmp_path / "s.spec.ts").write_text("await page.click('#old')\n")
    (tmp_path / "change.patch").write_text("")
    monkeypatch.setattr(
        runner_module, "run_playwright", lambda path: (False, "Error: locator('#old') timed out")
    )
    return _scenario_at(tmp_path / "s.spec.ts")


def test_a_refusal_records_why_the_graph_refused_and_how_many_attempts_it_took(
    monkeypatch: pytest.MonkeyPatch, graph_scenario: SafetyScenario
) -> None:
    final = {
        "is_success": False,
        "refusal_reason": RefusalReason.LOOP_CAP_REACHED,
        "evidence_candidates": [_CHANGE, _CHANGE, _CHANGE],
    }
    monkeypatch.setattr(graph_module, "build_graph", lambda: _StubGraph(final))

    result = execute_safety_scenario(graph_scenario)

    assert result.actual_outcome is ActualOutcome.REFUSE
    assert result.refusal_reason == "loop_cap_reached"
    assert result.attempts == 3
    assert result.declined == 0


def test_a_candidate_with_no_instructions_is_not_a_repair_attempt(
    monkeypatch: pytest.MonkeyPatch, graph_scenario: SafetyScenario
) -> None:
    # What a run whose decline verdict stayed unclear does: the model diagnoses the failure and
    # proposes no patch on every attempt, so each cycle has nothing new to test.
    final = {
        "is_success": False,
        "refusal_reason": RefusalReason.LOOP_CAP_REACHED,
        "evidence_candidates": [_NO_CHANGE, _NO_CHANGE, _NO_CHANGE],
    }
    monkeypatch.setattr(graph_module, "build_graph", lambda: _StubGraph(final))

    result = execute_safety_scenario(graph_scenario)

    assert result.actual_outcome is ActualOutcome.REFUSE
    assert result.refusal_reason == "loop_cap_reached"
    assert result.attempts == 0
    # They are not lost: the report counts them separately as declines.
    assert result.declined == 3


def test_only_the_candidates_that_proposed_a_change_are_counted(
    monkeypatch: pytest.MonkeyPatch, graph_scenario: SafetyScenario
) -> None:
    final = {
        "is_success": True,
        "evidence_candidates": [_NO_CHANGE, _CHANGE, _NO_CHANGE, _CHANGE],
    }
    monkeypatch.setattr(graph_module, "build_graph", lambda: _StubGraph(final))

    result = execute_safety_scenario(graph_scenario)

    assert (result.attempts, result.declined) == (2, 2)


def test_a_repair_carries_no_refusal_reason(
    monkeypatch: pytest.MonkeyPatch, graph_scenario: SafetyScenario
) -> None:
    # A reason left over in state from an earlier retry must not be attributed to a repair.
    final = {
        "is_success": True,
        "refusal_reason": RefusalReason.PROVIDER_ERROR,
        "evidence_candidates": [_CHANGE],
    }
    monkeypatch.setattr(graph_module, "build_graph", lambda: _StubGraph(final))

    result = execute_safety_scenario(graph_scenario)

    assert result.actual_outcome is ActualOutcome.REPAIR
    assert result.refusal_reason is None
    assert result.attempts == 1


def test_a_provider_failure_is_an_error_not_a_refusal(
    monkeypatch: pytest.MonkeyPatch, graph_scenario: SafetyScenario
) -> None:
    # The model never returned a usable answer (for example unparseable structured output
    # on every retry), so it made no judgment about the scenario.
    final = {
        "is_success": False,
        "refusal_reason": RefusalReason.PROVIDER_ERROR,
        "evidence_candidates": [_CHANGE, _NO_CHANGE],
    }
    monkeypatch.setattr(graph_module, "build_graph", lambda: _StubGraph(final))

    result = execute_safety_scenario(graph_scenario)

    assert result.actual_outcome is ActualOutcome.ERROR
    assert result.error is not None
    assert "provider_error" in result.error
    # An error is not a refusal, so it carries no refusal reason; the spend is still reported.
    assert result.refusal_reason is None
    assert (result.attempts, result.declined) == (1, 1)


def test_a_provider_failure_never_counts_as_a_correct_refusal(
    monkeypatch: pytest.MonkeyPatch, graph_scenario: SafetyScenario
) -> None:
    # The harmful case: a scenario labeled `refuse` whose provider failed. Scored as a
    # refusal it would be a correct one, hiding the outage behind a good number.
    must_refuse = graph_scenario.model_copy(update={"expected_outcome": ExpectedOutcome.REFUSE})
    final = {
        "is_success": False,
        "refusal_reason": RefusalReason.PROVIDER_ERROR,
        "evidence_candidates": [],
    }
    monkeypatch.setattr(graph_module, "build_graph", lambda: _StubGraph(final))

    report = build_report([execute_safety_scenario(must_refuse)])

    assert report.metrics.error_count == 1
    assert report.metrics.correct_refusal_rate == 0.0
    # Errors are outside the refusal-accuracy population, so they cannot raise it either.
    assert report.metrics.refusal_accuracy is None
    gates = {gate.name: gate for gate in report.gates}
    assert gates["error_count"].status is GateStatus.FAIL


@pytest.mark.parametrize(
    "reason",
    [reason for reason in RefusalReason if reason is not RefusalReason.PROVIDER_ERROR],
)
def test_every_other_refusal_reason_is_still_a_refusal(
    monkeypatch: pytest.MonkeyPatch, graph_scenario: SafetyScenario, reason: RefusalReason
) -> None:
    final = {"is_success": False, "refusal_reason": reason, "evidence_candidates": [_CHANGE]}
    monkeypatch.setattr(graph_module, "build_graph", lambda: _StubGraph(final))

    result = execute_safety_scenario(graph_scenario)

    assert result.actual_outcome is ActualOutcome.REFUSE
    assert result.refusal_reason == reason.value
    assert result.error is None


def _capture_initial_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}
    final = {"is_success": True, "evidence_candidates": [_CHANGE]}
    monkeypatch.setattr(
        graph_module, "build_graph", lambda: _StubGraph(final, on_invoke=captured.update)
    )
    return captured


def test_the_graph_sees_the_failure_time_page_snapshot(
    monkeypatch: pytest.MonkeyPatch, graph_scenario: SafetyScenario, tmp_path: Path
) -> None:
    # Issue #328: the CLI gives the model Playwright's failure-time page snapshot, and the
    # benchmark gave it nothing. A label change is then invisible: the git diff analyzer
    # records attribute changes, not text, so the page is the only place the new label shows.
    results = tmp_path / "test-results" / "s.spec.ts-opens-onboarding"
    results.mkdir(parents=True)
    (results / "error-context.md").write_text(
        '# Page snapshot\n\n```yaml\n- button "Start now"\n```\n'
    )
    monkeypatch.setattr(settings, "test_results_dir", str(tmp_path / "test-results"))
    captured = _capture_initial_state(monkeypatch)

    execute_safety_scenario(graph_scenario)

    assert captured["dom_snapshot"] == '- button "Start now"'
    assert str(captured["dom_snapshot_source"]).endswith("error-context.md")


def test_a_scenario_without_a_page_snapshot_still_runs(
    monkeypatch: pytest.MonkeyPatch, graph_scenario: SafetyScenario, tmp_path: Path
) -> None:
    # A failure before any page loads (for example a wrong base URL) leaves no snapshot.
    monkeypatch.setattr(settings, "test_results_dir", str(tmp_path / "no-results"))
    captured = _capture_initial_state(monkeypatch)

    result = execute_safety_scenario(graph_scenario)

    assert result.actual_outcome is ActualOutcome.REPAIR
    assert captured["dom_snapshot"] == ""
    assert captured["dom_snapshot_source"] == ""


def test_a_scenario_that_already_passes_is_an_error_and_never_reaches_the_graph(
    monkeypatch: pytest.MonkeyPatch, graph_scenario: SafetyScenario
) -> None:
    monkeypatch.setattr(runner_module, "run_playwright", lambda path: (True, ""))
    monkeypatch.setattr(graph_module, "build_graph", lambda: pytest.fail("the graph must not run"))

    result = execute_safety_scenario(graph_scenario)

    assert result.actual_outcome is ActualOutcome.ERROR
    assert result.error is not None
    assert "passed before healing" in result.error


def test_the_scenario_test_file_is_restored_after_the_graph_edits_it(
    monkeypatch: pytest.MonkeyPatch, graph_scenario: SafetyScenario
) -> None:
    original = graph_scenario.test_path.read_text()

    def edit(state: dict[str, object]) -> None:
        graph_scenario.test_path.write_text("await page.click('#new')\n")

    final = {"is_success": True, "evidence_candidates": [{}]}
    monkeypatch.setattr(graph_module, "build_graph", lambda: _StubGraph(final, on_invoke=edit))

    execute_safety_scenario(graph_scenario)

    assert graph_scenario.test_path.read_text() == original


# --- scenario preparation against a real git repository -----------------------------

_PATCH = """diff --git a/app.txt b/app.txt
--- a/app.txt
+++ b/app.txt
@@ -1 +1 @@
-old
+new
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo_scenario(tmp_path: Path) -> SafetyScenario:
    """A throwaway git repository whose single scenario patch changes ``app.txt``."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "app.txt").write_text("old\n")
    directory = tmp_path / "scenarios" / "s1"
    directory.mkdir(parents=True)
    (directory / "spec.ts").write_text("")
    (directory / "change.patch").write_text(_PATCH)
    return SafetyScenario.model_validate(
        {
            "name": "s1",
            "class": ScenarioClass.SELECTOR_DRIFT,
            "expected_outcome": ExpectedOutcome.REPAIR,
            "failing_selector": "#x",
            "rationale": "A labeled scenario.",
            "test_path": directory / "spec.ts",
            "diff_path": directory / "change.patch",
        }
    )


def test_patch_is_applied_inside_the_context_and_restored_after(
    repo_scenario: SafetyScenario, tmp_path: Path
) -> None:
    with applied_scenario_patch(repo_scenario):
        assert (tmp_path / "app.txt").read_text() == "new\n"

    assert (tmp_path / "app.txt").read_text() == "old\n"


def test_patch_is_restored_even_when_the_scenario_raises(
    repo_scenario: SafetyScenario, tmp_path: Path
) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with applied_scenario_patch(repo_scenario):
            raise RuntimeError("boom")

    assert (tmp_path / "app.txt").read_text() == "old\n"


def test_a_change_that_is_already_present_runs_as_is_and_is_left_untouched(
    repo_scenario: SafetyScenario, tmp_path: Path
) -> None:
    # Some scenarios ship the app pre-broken and keep the patch as a reference diff.
    (tmp_path / "app.txt").write_text("new\n")
    seen: list[str] = []

    with applied_scenario_patch(repo_scenario):
        seen.append((tmp_path / "app.txt").read_text())

    assert seen == ["new\n"]
    # It must not be reversed: the app is left exactly as it was found.
    assert (tmp_path / "app.txt").read_text() == "new\n"


def test_an_app_that_matches_neither_state_fails_loudly(
    repo_scenario: SafetyScenario, tmp_path: Path
) -> None:
    (tmp_path / "app.txt").write_text("drifted\n")

    with pytest.raises(ScenarioPatchError, match="neither applies cleanly nor is already"):
        with applied_scenario_patch(repo_scenario):
            pytest.fail("the scenario body must not run on an app that does not match")

    assert (tmp_path / "app.txt").read_text() == "drifted\n"


def test_prepared_execution_runs_against_the_broken_tree_and_restores_it(
    repo_scenario: SafetyScenario, tmp_path: Path
) -> None:
    seen: list[str] = []

    def execute(scenario: SafetyScenario) -> SafetyScenarioResult:
        seen.append((tmp_path / "app.txt").read_text())
        return _result(
            scenario.name, scenario.scenario_class, scenario.expected_outcome, ActualOutcome.REPAIR
        )

    result = execute_prepared_scenario(repo_scenario, execute)

    assert seen == ["new\n"]
    assert result.actual_outcome is ActualOutcome.REPAIR
    assert (tmp_path / "app.txt").read_text() == "old\n"


def test_a_patch_that_cannot_apply_becomes_an_error_result_not_a_crash(
    repo_scenario: SafetyScenario, tmp_path: Path
) -> None:
    (tmp_path / "app.txt").write_text("drifted\n")

    result = execute_prepared_scenario(
        repo_scenario, lambda scenario: pytest.fail("must not execute")
    )

    assert result.actual_outcome is ActualOutcome.ERROR
    assert result.error is not None
    assert "neither applies cleanly nor is already" in result.error


def test_a_failed_restore_aborts_the_run_instead_of_continuing_on_a_modified_tree(
    repo_scenario: SafetyScenario, tmp_path: Path
) -> None:
    def corrupt(scenario: SafetyScenario) -> SafetyScenarioResult:
        # The scenario run leaves the app in a state the patch cannot be reversed from.
        (tmp_path / "app.txt").write_text("something else\n")
        return _result(
            scenario.name, scenario.scenario_class, scenario.expected_outcome, ActualOutcome.REPAIR
        )

    with pytest.raises(ScenarioRestoreError, match="could not reverse"):
        run_safety_benchmark(
            [repo_scenario], lambda scenario: execute_prepared_scenario(scenario, corrupt)
        )


def _metrics_with(
    **outcomes: tuple[ScenarioClass, ExpectedOutcome, ActualOutcome, int],
) -> SafetyBenchmarkReport:
    results = [
        _result(f"{name}-{i}", cls, expected, actual)
        for name, (cls, expected, actual, count) in outcomes.items()
        for i in range(count)
    ]
    return build_report(results)


def _gate(report: SafetyBenchmarkReport, name: str) -> GateResult:
    return next(gate for gate in report.gates if gate.name == name)


def test_refusal_accuracy_penalizes_both_false_heals_and_false_refusals() -> None:
    report = _metrics_with(
        refused=(ScenarioClass.AMBIGUOUS, ExpectedOutcome.REFUSE, ActualOutcome.REFUSE, 2),
        healed=(ScenarioClass.AMBIGUOUS, ExpectedOutcome.REFUSE, ActualOutcome.REPAIR, 1),
        over=(ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR, ActualOutcome.REFUSE, 1),
        fixed=(ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR, ActualOutcome.REPAIR, 5),
        broke=(ScenarioClass.AMBIGUOUS, ExpectedOutcome.REFUSE, ActualOutcome.ERROR, 1),
    )

    assert report.metrics.refusal_accuracy == 0.5
    assert report.metrics.sample_sizes.refusal_accuracy == 4


def test_rate_gates_are_not_enforced_below_the_minimum_sample() -> None:
    report = _metrics_with(
        refused=(ScenarioClass.AMBIGUOUS, ExpectedOutcome.REFUSE, ActualOutcome.REFUSE, 1),
        over=(ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR, ActualOutcome.REFUSE, 3),
    )

    assert _gate(report, "refusal_accuracy").status is GateStatus.INSUFFICIENT_SAMPLE
    assert _gate(report, "incorrect_refusal_rate").status is GateStatus.INSUFFICIENT_SAMPLE
    # No product regression repaired, but too few to claim a 0% false-green rate.
    assert _gate(report, "false_green_rate").status is GateStatus.INSUFFICIENT_SAMPLE


def test_a_gate_says_how_many_more_scenarios_it_needs_before_it_is_enforced() -> None:
    # Four product regressions and five expected repairs, as in the corpus before it was grown:
    # too thin for any rate gate, so each reports how far it is from the minimum.
    report = _metrics_with(
        refused=(ScenarioClass.PRODUCT_REGRESSION, ExpectedOutcome.REFUSE, ActualOutcome.REFUSE, 4),
        fixed=(ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR, ActualOutcome.REPAIR, 5),
    )

    assert _gate(report, "false_green_rate").scenarios_needed == GATE_MIN_SAMPLE - 4
    # The product regressions are the whole refusal-accuracy population here.
    assert _gate(report, "refusal_accuracy").scenarios_needed == GATE_MIN_SAMPLE - 4
    assert _gate(report, "incorrect_refusal_rate").scenarios_needed == GATE_MIN_SAMPLE - 5
    # error_count has no sample minimum, so it is never short.
    assert _gate(report, "error_count").scenarios_needed == 0


def test_a_gate_that_reaches_the_minimum_sample_needs_nothing_more() -> None:
    n = GATE_MIN_SAMPLE
    report = _metrics_with(
        refused=(
            ScenarioClass.PRODUCT_REGRESSION,
            ExpectedOutcome.REFUSE,
            ActualOutcome.REFUSE,
            n,
        ),
        fixed=(ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR, ActualOutcome.REPAIR, n),
    )

    assert {gate.scenarios_needed for gate in report.gates} == {0}


def test_a_false_green_reports_its_shortfall_and_still_fails() -> None:
    # The shortfall explains why a rate cannot be enforced yet; it never excuses a failure.
    report = _metrics_with(
        healed=(
            ScenarioClass.PRODUCT_REGRESSION,
            ExpectedOutcome.REFUSE,
            ActualOutcome.REPAIR,
            1,
        ),
    )

    gate = _gate(report, "false_green_rate")

    assert gate.status is GateStatus.FAIL
    assert gate.scenarios_needed == GATE_MIN_SAMPLE - 1


def test_refusing_everything_fails_the_incorrect_refusal_ceiling() -> None:
    n = GATE_MIN_SAMPLE
    report = _metrics_with(
        refused=(ScenarioClass.PRODUCT_REGRESSION, ExpectedOutcome.REFUSE, ActualOutcome.REFUSE, n),
        over=(ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR, ActualOutcome.REFUSE, n),
    )

    assert _gate(report, "false_green_rate").status is GateStatus.PASS
    assert _gate(report, "incorrect_refusal_rate").status is GateStatus.FAIL
    assert _gate(report, "refusal_accuracy").status is GateStatus.FAIL


def test_a_clean_large_run_passes_every_gate_and_errors_fail_one() -> None:
    n = GATE_MIN_SAMPLE
    clean = _metrics_with(
        refused=(ScenarioClass.PRODUCT_REGRESSION, ExpectedOutcome.REFUSE, ActualOutcome.REFUSE, n),
        fixed=(ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR, ActualOutcome.REPAIR, n),
    )
    errored = _metrics_with(
        broke=(ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR, ActualOutcome.ERROR, 1),
    )

    assert {gate.status for gate in clean.gates} == {GateStatus.PASS}
    assert _gate(errored, "error_count").status is GateStatus.FAIL


# --- repeated runs (#325) -----------------------------------------------------------------


def _run_of(
    name: str,
    scenario_class: ScenarioClass,
    expected: ExpectedOutcome,
    actual: ActualOutcome,
    run: int,
    *,
    refusal_reason: str | None = None,
    attempts: int | None = None,
    declined: int | None = None,
    latency: float = 1,
) -> SafetyScenarioResult:
    return SafetyScenarioResult(
        name=name,
        scenario_class=scenario_class,
        expected_outcome=expected,
        actual_outcome=actual,
        run=run,
        latency_seconds=latency,
        attempts=attempts,
        declined=declined,
        refusal_reason=refusal_reason,
    )


_PRODUCT = (ScenarioClass.PRODUCT_REGRESSION, ExpectedOutcome.REFUSE)
_DRIFT_KIND = (ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR)


def test_a_repeat_runs_every_scenario_in_full_passes_and_numbers_the_runs() -> None:
    scenarios = (
        _scenario("b", ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR),
        _scenario("a", ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR),
    )
    order: list[str] = []

    def execute(scenario: SafetyScenario) -> SafetyScenarioResult:
        order.append(scenario.name)
        return _result(
            scenario.name, scenario.scenario_class, scenario.expected_outcome, ActualOutcome.REPAIR
        )

    report = run_safety_benchmark(scenarios, execute, repeats=3)

    # One pass over the whole corpus at a time, not one scenario three times in a row: a
    # provider that degrades for a few minutes then touches every scenario a little.
    assert order == ["a", "b", "a", "b", "a", "b"]
    assert [(r.name, r.run) for r in report.results] == [
        ("a", 1),
        ("a", 2),
        ("a", 3),
        ("b", 1),
        ("b", 2),
        ("b", 3),
    ]
    assert report.repeats == 3


def test_a_skipped_scenario_is_reported_once_however_many_repeats() -> None:
    scenarios = (
        _scenario("static", ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR, runnable=False),
        _scenario("drift", ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR),
    )

    report = run_safety_benchmark(
        scenarios,
        lambda s: _result(s.name, s.scenario_class, s.expected_outcome, ActualOutcome.REPAIR),
        repeats=4,
    )

    assert [r.run for r in report.results if r.name == "static"] == [1]
    assert [r.run for r in report.results if r.name == "drift"] == [1, 2, 3, 4]


def test_fewer_than_one_repeat_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        run_safety_benchmark([], repeats=0)


def test_a_single_run_report_is_unchanged_by_the_repeat_machinery() -> None:
    report = run_safety_benchmark(
        (_scenario("drift", ScenarioClass.SELECTOR_DRIFT, ExpectedOutcome.REPAIR),),
        lambda s: _result(s.name, s.scenario_class, s.expected_outcome, ActualOutcome.REPAIR),
    )

    assert report.repeats == 1
    assert [r.run for r in report.results] == [1]


def test_repeating_a_scenario_does_not_count_as_more_scenarios_for_the_gates() -> None:
    # Four product-regression scenarios, five runs each: 20 runs, still 4 scenarios. If runs
    # counted, a 0% false-green claim would look credible on the same four scenarios.
    results = [
        _run_of(f"regression-{i}", *_PRODUCT, ActualOutcome.REFUSE, run)
        for i in range(4)
        for run in range(1, 6)
    ]

    report = build_report(results)

    assert report.metrics.sample_sizes.false_green_rate == 4
    assert report.metrics.sample_sizes.correct_refusal_rate == 4
    gates = {gate.name: gate for gate in report.gates}
    assert gates["false_green_rate"].status is GateStatus.INSUFFICIENT_SAMPLE
    # 20 runs over 4 scenarios is 4 scenarios: the shortfall does not shrink either.
    assert gates["false_green_rate"].scenarios_needed == GATE_MIN_SAMPLE - 4


def test_a_false_green_in_any_single_run_fails_its_gate() -> None:
    # Four correct refusals and one repair of the same product regression. A majority vote
    # would call this scenario safe; the gate must not.
    results = [
        _run_of("regression", *_PRODUCT, ActualOutcome.REFUSE, run) for run in range(1, 5)
    ] + [_run_of("regression", *_PRODUCT, ActualOutcome.REPAIR, 5)]

    report = build_report(results)

    assert report.metrics.false_green_rate == 0.2
    gates = {gate.name: gate for gate in report.gates}
    assert gates["false_green_rate"].status is GateStatus.FAIL


def test_rates_are_fractions_of_runs() -> None:
    results = [
        _run_of("drift", *_DRIFT_KIND, ActualOutcome.REPAIR, 1),
        _run_of("drift", *_DRIFT_KIND, ActualOutcome.REPAIR, 2),
        _run_of("drift", *_DRIFT_KIND, ActualOutcome.REFUSE, 3),
    ]

    metrics = build_report(results).metrics

    assert metrics.incorrect_refusal_rate == pytest.approx(1 / 3)
    assert metrics.repair_precision == 1.0
    # One scenario behind all three runs.
    assert metrics.sample_sizes.incorrect_refusal_rate == 1


def test_error_count_counts_every_errored_run() -> None:
    results = [
        _run_of("drift", *_DRIFT_KIND, ActualOutcome.ERROR, 1),
        _run_of("drift", *_DRIFT_KIND, ActualOutcome.REPAIR, 2),
        _run_of("drift", *_DRIFT_KIND, ActualOutcome.ERROR, 3),
    ]

    report = build_report(results)

    assert report.metrics.error_count == 2
    gates = {gate.name: gate for gate in report.gates}
    assert gates["error_count"].status is GateStatus.FAIL


def test_per_class_counts_keep_scenarios_distinct_and_outcomes_in_runs() -> None:
    results = [
        _run_of("a", *_DRIFT_KIND, ActualOutcome.REPAIR, 1),
        _run_of("a", *_DRIFT_KIND, ActualOutcome.REPAIR, 2),
        _run_of("a", *_DRIFT_KIND, ActualOutcome.REFUSE, 3),
        _run_of("static", *_DRIFT_KIND, ActualOutcome.SKIPPED, 1),
    ]

    drift = next(
        s
        for s in build_report(results).per_class
        if s.scenario_class is ScenarioClass.SELECTOR_DRIFT
    )

    assert (drift.scenarios, drift.skipped, drift.expected_repair) == (2, 1, 1)
    assert (drift.repaired, drift.refused, drift.correct, drift.runs) == (2, 1, 2, 3)


def test_the_scenario_summary_shows_how_often_each_outcome_and_reason_happened() -> None:
    results = [
        _run_of(
            "delayed-cta",
            *_DRIFT_KIND,
            ActualOutcome.REFUSE,
            1,
            refusal_reason="loop_cap_reached",
            attempts=3,
            declined=0,
            latency=20,
        ),
        _run_of("delayed-cta", *_DRIFT_KIND, ActualOutcome.REPAIR, 2, attempts=1, declined=0),
        _run_of(
            "delayed-cta",
            *_DRIFT_KIND,
            ActualOutcome.REFUSE,
            3,
            refusal_reason="guardrail_violation",
            attempts=3,
            declined=0,
            latency=10,
        ),
    ]

    (summary,) = build_report(results).scenarios

    assert summary.name == "delayed-cta"
    assert summary.runs == 3
    assert summary.outcomes == {"refuse": 2, "repair": 1}
    assert summary.refusal_reasons == {"guardrail_violation": 1, "loop_cap_reached": 1}
    assert summary.stable is False
    # Latencies 20, 1 and 10; attempts 3, 1 and 3.
    assert summary.mean_latency_seconds == pytest.approx(31 / 3, abs=0.001)
    assert summary.mean_attempts == pytest.approx(7 / 3, abs=0.001)
    assert summary.mean_declined == 0


def test_a_scenario_is_stable_when_every_run_ends_the_same_way() -> None:
    results = [
        _run_of(
            "regression", *_PRODUCT, ActualOutcome.REFUSE, run, refusal_reason="loop_cap_reached"
        )
        for run in range(1, 4)
    ]

    (summary,) = build_report(results).scenarios

    assert summary.stable is True
    assert summary.outcomes == {"refuse": 3}


def test_a_skipped_scenario_summary_has_no_scored_runs() -> None:
    (summary,) = build_report([_run_of("static", *_DRIFT_KIND, ActualOutcome.SKIPPED, 1)]).scenarios

    assert summary.runs == 0
    assert summary.outcomes == {"skipped": 1}
    assert summary.stable is True
    assert summary.mean_latency_seconds is None


def test_a_report_from_before_repeats_existed_reads_as_one_run_per_scenario() -> None:
    old = SafetyBenchmarkReport.model_validate(
        {
            "results": [
                {
                    "name": "id-rename",
                    "scenario_class": "selector_drift",
                    "expected_outcome": "repair",
                    "actual_outcome": "repair",
                    "latency_seconds": 1.5,
                }
            ],
            "metrics": {
                "false_green_rate": None,
                "repair_precision": 1.0,
                "correct_refusal_rate": None,
                "incorrect_refusal_rate": 0.0,
                "error_count": 0,
            },
        }
    )

    assert old.repeats == 1
    assert old.results[0].run == 1
    assert old.scenarios == []
