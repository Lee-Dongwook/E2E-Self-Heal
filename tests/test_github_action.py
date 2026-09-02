from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[1]
ACTION_FILE = REPOSITORY_ROOT / "action.yml"
CONSUMER_FIXTURE = REPOSITORY_ROOT / "ci" / "action-consumer"
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"


def test_action_uses_its_checkout_for_all_healer_commands() -> None:
    action = ACTION_FILE.read_text(encoding="utf-8")

    assert 'uv lock --check --project "$GITHUB_ACTION_PATH"' in action
    assert 'uv sync --locked --project "$GITHUB_ACTION_PATH"' in action
    assert 'healer=(uv run --project "$GITHUB_ACTION_PATH" --no-sync e2e-healer)' in action
    assert "uv run e2e-healer" not in action


def test_action_consumer_fixture_is_javascript_only() -> None:
    assert (CONSUMER_FIXTURE / "package.json").is_file()
    assert (CONSUMER_FIXTURE / "smoke.mjs").is_file()
    assert (CONSUMER_FIXTURE / "tests" / "example.spec.js").is_file()
    assert not (CONSUMER_FIXTURE / "pyproject.toml").exists()
    assert not (CONSUMER_FIXTURE / "uv.lock").exists()


def test_ci_runs_the_action_from_the_separate_consumer_fixture() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "action-consumer:" in workflow
    assert "permissions:\n      contents: read" in workflow
    assert "path: action" in workflow
    assert "persist-credentials: false" in workflow
    assert 'action/ci/action-consumer "$GITHUB_WORKSPACE/consumer"' in workflow
    assert 'git -C "$GITHUB_WORKSPACE/consumer" init' in workflow
    assert "uses: ./action" in workflow
    assert "working-directory: consumer" in workflow


def test_ci_uses_non_mutating_quality_gates() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "run: make check" in workflow
    assert "run: make coverage" in workflow
    assert "git-auto-commit-action" not in workflow
    assert "ruff format ." not in workflow
