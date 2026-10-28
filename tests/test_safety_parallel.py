"""The parallel safety benchmark: isolated project copies, one per worker process."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import structlog

from app.safety_benchmark import (
    ActualOutcome,
    SafetyScenario,
    SafetyScenarioResult,
    discover_safety_scenarios,
    execute_prepared_scenario,
)
from app.safety_parallel import ParallelSetupError, run_parallel_safety_benchmark

# Every scenario rewrites the same line differently, so two of them applied to one tree at the
# same time would conflict. Only isolated copies let them run concurrently.
_PATCH = """diff --git a/proj/app.txt b/proj/app.txt
--- a/proj/app.txt
+++ b/proj/app.txt
@@ -1 +1 @@
-old
+{new}
"""


def _write_scenario(root: Path, name: str, patch: str) -> None:
    directory = root / "proj" / "scenarios" / name
    directory.mkdir(parents=True)
    (directory / "spec.ts").write_text("")
    (directory / "change.patch").write_text(patch)
    (directory / "meta.json").write_text(
        json.dumps(
            {
                "class": "selector_drift",
                "expected_outcome": "repair",
                "failing_selector": "#x",
                "rationale": "A labeled scenario.",
            }
        )
    )


def _seen_by_worker(scenario: SafetyScenario) -> SafetyScenarioResult:
    """Record, from inside the worker, what its copy and the real project look like."""
    copy_app = scenario.test_path.parents[2] / "app.txt"
    real_app = Path(os.environ["SAFETY_PARALLEL_REAL_APP"])
    print(f"worker {os.getpid()} ran {scenario.name}")
    return SafetyScenarioResult(
        name=scenario.name,
        scenario_class=scenario.scenario_class,
        expected_outcome=scenario.expected_outcome,
        actual_outcome=ActualOutcome.REPAIR,
        latency_seconds=0,
        error=f"{copy_app.read_text().strip()}|{real_app.read_text().strip()}|{os.getcwd()}",
    )


def _break_and_look(scenario: SafetyScenario) -> SafetyScenarioResult:
    return execute_prepared_scenario(scenario, _seen_by_worker)


_CREDENTIAL = "nvapi-WORKERLOG0123456789abcdef"


def _provider_fails_holding_a_credential(scenario: SafetyScenario) -> SafetyScenarioResult:
    """Fail the way a provider call does: an exception raised in a frame that holds the key."""
    params = {"model": "m", "api_key": _CREDENTIAL}
    try:
        raise RuntimeError("provider rejected model " + params["model"])
    except RuntimeError:
        structlog.get_logger("worker").exception("patch_generation_failed")
    return SafetyScenarioResult(
        name=scenario.name,
        scenario_class=scenario.scenario_class,
        expected_outcome=scenario.expected_outcome,
        actual_outcome=ActualOutcome.ERROR,
        latency_seconds=0,
        error="provider failed",
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "app.txt").write_text("old\n")
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "reporter.ts").write_text("// read by the project config\n")
    for index in range(4):
        _write_scenario(tmp_path, f"s{index}", _PATCH.format(new=f"new-{index}"))
    monkeypatch.chdir(tmp_path / "proj")
    monkeypatch.setenv("SAFETY_PARALLEL_REAL_APP", str(tmp_path / "proj" / "app.txt"))
    return tmp_path


def test_each_scenario_breaks_its_own_copy_and_never_the_project(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    log_dir = tmp_path_factory.mktemp("logs")
    seen: list[str] = []

    report = run_parallel_safety_benchmark(
        discover_safety_scenarios(Path("scenarios")),
        jobs=2,
        repeats=2,
        log_dir=log_dir,
        executor=_break_and_look,
        on_result=lambda result: seen.append(f"{result.name}/{result.run}"),
    )

    assert [(r.name, r.run) for r in report.results] == [
        (f"s{i}", run) for i in range(4) for run in (1, 2)
    ]
    for result in report.results:
        copy_app, real_app, cwd = (result.error or "").split("|")
        assert copy_app == f"new-{result.name[1:]}"
        assert real_app == "old"
        assert Path(cwd) != project / "proj"
    assert sorted(seen) == sorted(f"s{i}/{run}" for i in range(4) for run in (1, 2))
    # The project is exactly as it was, and the copies are gone.
    assert (project / "proj" / "app.txt").read_text() == "old\n"
    logs = "".join(path.read_text() for path in log_dir.glob("worker-*.log"))
    assert logs.count(" ran s") == 8


def test_a_worker_log_never_contains_a_credential_from_a_failing_frame(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # CI uploads these logs as an artifact, which GitHub's secret masking does not cover.
    log_dir = tmp_path_factory.mktemp("logs")

    run_parallel_safety_benchmark(
        discover_safety_scenarios(Path("scenarios")),
        jobs=2,
        log_dir=log_dir,
        executor=_provider_fails_holding_a_credential,
    )

    logs = "".join(path.read_text() for path in log_dir.glob("worker-*.log"))
    # The failure is still diagnosable from the log, without the frame's locals.
    assert "RuntimeError: provider rejected model m" in logs
    assert _CREDENTIAL not in logs


def test_a_patch_outside_the_project_is_rejected_before_anything_runs(
    project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = _PATCH.replace("proj/app.txt", "shared/reporter.ts").format(new="x")
    _write_scenario(project, "z-outside", outside)

    with pytest.raises(ParallelSetupError, match="z-outside.*shared/reporter.ts"):
        run_parallel_safety_benchmark(
            discover_safety_scenarios(Path("scenarios")),
            jobs=2,
            log_dir=tmp_path_factory.mktemp("logs"),
            executor=_break_and_look,
        )
    assert (project / "shared" / "reporter.ts").read_text() == "// read by the project config\n"


def test_the_repository_root_cannot_be_the_copied_project(
    project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    scenarios = discover_safety_scenarios(project / "proj" / "scenarios")
    monkeypatch.chdir(project)

    with pytest.raises(ParallelSetupError, match="project directory"):
        run_parallel_safety_benchmark(
            scenarios, jobs=2, log_dir=tmp_path_factory.mktemp("logs"), executor=_break_and_look
        )
