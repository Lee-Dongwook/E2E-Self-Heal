"""Pin the properties #291 requires of the safety benchmark workflow.

The workflow cannot run in the unit-test environment (it needs a model key, a browser and a
CI runner), so these tests guard the parts that must not regress silently: it never blocks,
it is skipped without credentials, and it does not expose a secret to forked pull requests.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_WORKFLOW = Path(".github/workflows/safety-benchmark.yml")


@pytest.fixture(scope="module")
def workflow() -> dict[Any, Any]:
    return yaml.safe_load(_WORKFLOW.read_text())


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # YAML 1.1 parses the bare key `on` as the boolean True.
    return workflow["on"] if "on" in workflow else workflow[True]


@pytest.fixture(scope="module")
def job(workflow: dict[Any, Any]) -> dict[str, Any]:
    return workflow["jobs"]["safety-benchmark"]


def _steps_running(job: dict[str, Any], text: str) -> list[dict[str, Any]]:
    return [step for step in job["steps"] if text in step.get("run", "")]


def test_the_job_can_never_block_a_merge(job: dict[str, Any]) -> None:
    assert job["continue-on-error"] is True


def test_the_job_is_time_bounded(job: dict[str, Any]) -> None:
    assert job["timeout-minutes"] <= 120


def test_runs_for_different_refs_take_turns_on_the_shared_key(job: dict[str, Any]) -> None:
    # One free key serves every ref: a per-ref job group would let main and pull requests call it
    # at once, and cancelling in progress would let a pull request kill a running baseline run.
    concurrency = job["concurrency"]
    assert "${{" not in concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_the_job_is_skipped_rather_than_failed_without_the_model_key(
    workflow: dict[Any, Any], job: dict[str, Any]
) -> None:
    assert job["needs"] == "check-secret"
    assert "needs.check-secret.outputs.has-key == 'true'" in job["if"]
    check = workflow["jobs"]["check-secret"]
    assert "has-key" in check["outputs"]
    # The probe reads the secret into an environment variable and only ever compares it.
    env = check["steps"][0]["env"]
    assert env == {"E2E_HEALER_GEMINI_API_KEY": "${{ secrets.E2E_HEALER_GEMINI_API_KEY }}"}


def test_the_secret_is_never_exposed_to_forked_pull_requests(workflow: dict[Any, Any]) -> None:
    triggers = _triggers(workflow)

    assert "pull_request" in triggers
    # pull_request_target would run with secrets against code from a fork.
    assert "pull_request_target" not in triggers


def test_the_workflow_has_read_only_permissions(workflow: dict[Any, Any]) -> None:
    assert workflow["permissions"] == {"contents": "read"}


def test_pull_requests_only_run_it_when_repair_behaviour_can_change(
    workflow: dict[Any, Any],
) -> None:
    paths = _triggers(workflow)["pull_request"]["paths"]

    assert "app/**" in paths
    assert "examples/**" in paths
    # Docs-only changes must not spend model calls.
    assert not any(path.startswith("docs") for path in paths)


def test_the_benchmark_breaks_the_demo_app_itself_and_avoids_the_review_reporter(
    job: dict[str, Any],
) -> None:
    (step,) = _steps_running(job, "safety-benchmark")

    # Without --apply-patches every scenario would error with "passed before healing".
    assert "--apply-patches" in step["run"]
    assert "--output" in step["run"]
    assert "--markdown" in step["run"]
    assert "--baseline" in step["run"]
    # The example config's second reporter runs `e2e-healer review` on every failure.
    assert "--reporter=list" in step["env"]["E2E_HEALER_PLAYWRIGHT_CMD"]
    assert step["working-directory"] == "examples"


def test_the_sandbox_lets_the_engine_edit_the_scenarios_named_spec_ts(
    job: dict[str, Any],
) -> None:
    (step,) = _steps_running(job, "safety-benchmark")
    globs = step["env"]["E2E_HEALER_WRITE_GLOBS"].split(",")

    # The default globs only match `*.spec.ts`; the scenarios are literally named spec.ts.
    assert "**/spec.ts" in globs
    # It must stay a narrow allowance, not a wildcard over the workspace.
    assert not any(glob in {"*", "**/*", "**"} for glob in globs)


def _uploads(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    steps = [step for step in job["steps"] if "upload-artifact" in step.get("uses", "")]
    return {step["with"]["name"]: step for step in steps}


def test_the_report_is_published_even_when_the_benchmark_step_fails(job: dict[str, Any]) -> None:
    (summary,) = _steps_running(job, "GITHUB_STEP_SUMMARY")

    assert summary["if"] == "always()"
    assert _uploads(job)["safety-report"]["if"] == "always()"


def test_worker_logs_are_published_only_after_they_were_redacted(job: dict[str, Any]) -> None:
    # An artifact is not covered by GitHub's secret masking, so the logs must fail closed: a
    # redaction step that errors halfway leaves raw files, which must never be uploaded.
    (redact,) = [step for step in job["steps"] if step.get("id") == "redact"]
    logs = _uploads(job)["safety-worker-logs"]

    assert "steps.redact.outcome == 'success'" in logs["if"]
    assert "e2e-healer-safety-logs" not in str(_uploads(job)["safety-report"]["with"]["path"])


def test_the_workflow_sanitizer_removes_a_key_written_straight_to_a_worker_log(
    job: dict[str, Any], tmp_path: Path
) -> None:
    # Structured logs are redacted in the worker; raw stdout/stderr writes only by this step.
    # Run the step's own script, so emptying or breaking it fails here and not in CI.
    (redact,) = [step for step in job["steps"] if step.get("id") == "redact"]
    script = redact["run"].split("<<'EOF'\n", 1)[1].rsplit("EOF", 1)[0]
    key = "AIzaRAWWRITE0123456789abcdefghijklmnop"
    logs = tmp_path / "e2e-healer-safety-logs-x"
    logs.mkdir()
    log = logs / "worker-0.log"
    log.write_text(f"print from a dependency: {key}\nPlaywright line\n", encoding="utf-8")

    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        env={
            **os.environ,
            **{name: value for name, value in redact["env"].items() if "secrets." not in value},
            "RUNNER_TEMP": str(tmp_path),
            "E2E_HEALER_LLM_API_KEY": key,
        },
    )

    redacted = log.read_text(encoding="utf-8")
    assert key not in redacted
    assert "Playwright line" in redacted
