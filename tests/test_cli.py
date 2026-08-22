import json
import re
import subprocess
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch
import pytest
from typer.testing import CliRunner
import app.cli as cli_module
from app.cli import app
from app.healing_history import load_history
from app.sandbox import SandboxViolation
from app.schemas import SCHEMA_VERSION, RepairSummary, SuiteSummary
from app.state import AgentState
from app.utils.files import atomic_write

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Drop ANSI color codes so substring checks survive rich's styled help output.
    Under color (CI forces it), rich splits an option like --shadow into separately
    styled spans (- + -shadow), so a raw substring match fails. Stripping the
    escape codes first makes the assertion terminal-independent.
    """
    return _ANSI_RE.sub("", text)


@pytest.fixture
def mock_graph_success(monkeypatch):
    class MockGraph:
        def invoke(self, state):
            state["is_success"] = True
            state["loop_count"] = 1
            state["current_code"] = "await page.click('#new')"
            state["patch_instructions"] = {
                "instructions": [
                    {
                        "line": 1,
                        "original": "await page.click('#old')",
                        "replacement": "await page.click('#new')",
                        "reason": "fixed",
                    }
                ]
            }

            if state["test_script_path"]:
                atomic_write(Path(state["test_script_path"]), state["current_code"])
            return state

    monkeypatch.setattr(cli_module, "build_graph", lambda: MockGraph())


@pytest.fixture
def mock_graph_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class MockGraph:
        def invoke(self, state: AgentState) -> AgentState:
            state["is_success"] = False
            state["loop_count"] = 3
            state["current_code"] = "await page.click('#new')"
            state["patch_instructions"] = {}
            if state["test_script_path"]:
                atomic_write(Path(state["test_script_path"]), state["current_code"])
            return state

    monkeypatch.setattr(cli_module, "build_graph", lambda: MockGraph())


@pytest.fixture
def mock_graph_crash_after_write(monkeypatch: pytest.MonkeyPatch) -> None:
    class MockGraph:
        def invoke(self, state: dict) -> dict:
            candidate = "await page.click('#new')"
            state["current_code"] = candidate
            if state["test_script_path"]:
                atomic_write(Path(state["test_script_path"]), candidate)
            raise OSError("probe: post-write failure")

    monkeypatch.setattr(cli_module, "build_graph", lambda: MockGraph())


@pytest.fixture
def mock_review_graph(monkeypatch):
    class MockGraph:
        def invoke(self, state):
            state["review_report"] = {
                "findings": [
                    {
                        "file": "components/CTAButton.tsx",
                        "line": 12,
                        "broken_selector": "#cta",
                        "root_cause": "className renamed",
                        "suggestion": "add a stable data-testid",
                        "recommended_selector": "getByTestId('cta')",
                        "severity": "warning",
                    }
                ]
            }
            return state

    monkeypatch.setattr(cli_module, "build_review_graph", lambda: MockGraph())


def test_cli_review_emits_report_and_leaves_file_unmodified(
    mock_review_graph, monkeypatch, tmp_path
) -> None:
    test_file = tmp_path / "test.spec.ts"
    original = "await page.click('#cta')"
    test_file.write_text(original)
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error waiting for selector")
    runner = CliRunner()
    result = runner.invoke(app, ["review", str(test_file), "--log", str(log_file), "--json"])
    assert result.exit_code == 0
    json_line = next(line for line in result.stdout.splitlines() if line.strip().startswith("{"))
    data = json.loads(json_line)
    assert data["has_findings"] is True
    assert data["findings"][0]["file"] == "components/CTAButton.tsx"
    assert data["findings"][0]["recommended_selector"] == "getByTestId('cta')"
    # Contract: review mode emits a self-describing review report.
    assert data["kind"] == "review"
    assert data["schema_version"] == SCHEMA_VERSION
    # review mode is advisory only — the test file must be untouched.
    assert test_file.read_text() == original


def test_cli_review_test_path_not_exists() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["review", "nonexistent_file.spec.ts"])
    assert result.exit_code == 2
    assert "path not found:" in result.stderr


def test_cli_test_path_not_exists() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["nonexistent_file.spec.ts"])
    assert result.exit_code == 2
    assert "path not found:" in result.stderr


def test_cli_single_file_already_passes(monkeypatch, tmp_path) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#btn')")
    monkeypatch.setattr(cli_module, "run_playwright", lambda path: (True, "Passed!"))
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file)])
    assert result.exit_code == 0
    assert "test already passes" in result.stderr


def test_cli_single_file_healed_success(mock_graph_success, monkeypatch, tmp_path) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error waiting for selector")
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file)])
    assert result.exit_code == 0
    assert "fixed after 1 loop(s)" in result.stderr
    assert test_file.read_text() == "await page.click('#new')"


def test_cli_single_file_healed_failed(mock_graph_failure, monkeypatch, tmp_path) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error waiting for selector")
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file)])
    assert result.exit_code == 1
    assert "not fixed after 3 loop(s)" in result.stderr
    assert test_file.read_text() == "await page.click('#old')"


def test_cli_help_documents_memory_toggle() -> None:
    result = CliRunner().invoke(app, ["heal", "--help"])

    assert result.exit_code == 0
    assert "--memory" in _strip_ansi(result.stdout)
    assert "--no-memory" in _strip_ansi(result.stdout)


def test_heal_file_no_memory_bypasses_persistence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_file = tmp_path / "test.spec.ts"
    original = "await page.click('#old')"
    replacement = "await page.click('#new')"
    test_file.write_text(original)

    class MockGraph:
        def invoke(self, state: AgentState) -> AgentState:
            assert state.get("memory_enabled", True) is False
            state.update(
                {
                    "is_success": True,
                    "current_code": replacement,
                    "patch_instructions": {
                        "instructions": [
                            {
                                "line": 1,
                                "original": original,
                                "replacement": replacement,
                                "reason": "selector renamed",
                                "selector": "#new",
                            }
                        ]
                    },
                }
            )
            atomic_write(Path(state["test_script_path"]), replacement)
            return state

    monkeypatch.setattr(cli_module, "build_graph", lambda: MockGraph())
    monkeypatch.setattr(
        cli_module,
        "append_record",
        lambda _: pytest.fail("--no-memory must not persist healing history"),
    )

    assert (
        cli_module._heal_file(
            test_file,
            "Error: waiting for locator('#old') timed out",
            [],
            dry_run=False,
            memory_enabled=False,
        ).is_success
        is True
    )


def test_cli_no_memory_passes_disabled_state_to_the_graph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Error: waiting for locator('#old') timed out")
    seen_memory_enabled: list[bool] = []

    class MockGraph:
        def invoke(self, state: AgentState) -> AgentState:
            seen_memory_enabled.append(state.get("memory_enabled", True))
            state.update(
                {
                    "is_success": True,
                    "current_code": "await page.click('#new')",
                    "patch_instructions": {},
                }
            )
            atomic_write(Path(state["test_script_path"]), state["current_code"])
            return state

    monkeypatch.setattr(cli_module, "build_graph", lambda: MockGraph())

    result = CliRunner().invoke(app, [str(test_file), "--log", str(log_file), "--no-memory"])

    assert result.exit_code == 0
    assert seen_memory_enabled == [False]


def test_cli_dry_run_restores_file(mock_graph_success, monkeypatch, tmp_path) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error waiting for selector")
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file), "--dry-run"])
    assert result.exit_code == 0
    assert "fixed after 1 loop(s)" in result.stderr
    assert test_file.read_text() == "await page.click('#old')"


def test_heal_file_records_only_committed_selector_repairs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_file = tmp_path / "test.spec.ts"
    original = "await page.click('#old')"
    replacement = "await page.click('#new')"
    test_file.write_text(original)
    monkeypatch.setattr(cli_module.settings, "workspace_root", str(tmp_path))
    monkeypatch.setattr(cli_module.settings, "sandbox_mode", "strict")

    class MockGraph:
        def invoke(self, state: AgentState) -> AgentState:
            state.update(
                {
                    "is_success": True,
                    "current_code": replacement,
                    "patch_instructions": {
                        "instructions": [
                            {
                                "line": 1,
                                "original": original,
                                "replacement": replacement,
                                "reason": "selector renamed",
                                "selector": "#new",
                            }
                        ]
                    },
                }
            )
            atomic_write(Path(state["test_script_path"]), replacement)
            return state

    monkeypatch.setattr(cli_module, "build_graph", lambda: MockGraph())
    error = "Error: waiting for locator('#old') timed out"

    assert cli_module._heal_file(test_file, error, [], dry_run=False).is_success is True
    assert len(load_history().records) == 1

    test_file.write_text(original)
    assert cli_module._heal_file(test_file, error, [], dry_run=True).is_success is True
    assert test_file.read_text() == original
    assert len(load_history().records) == 1


def test_heal_file_does_not_rerecord_memory_repairs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_file = tmp_path / "test.spec.ts"
    original = "await page.click('#old')"
    replacement = "await page.click('#new')"
    test_file.write_text(original)

    class MemoryGraph:
        def invoke(self, state: dict) -> dict:
            state.update(
                {
                    "is_success": True,
                    "current_code": replacement,
                    "memory_report": {"source": "memory"},
                    "patch_instructions": {
                        "instructions": [
                            {
                                "line": 1,
                                "original": original,
                                "replacement": replacement,
                                "reason": "history candidate",
                                "selector": "#new",
                            }
                        ]
                    },
                }
            )
            atomic_write(Path(state["test_script_path"]), replacement)
            return state

    monkeypatch.setattr(cli_module, "build_graph", lambda: MemoryGraph())
    monkeypatch.setattr(
        cli_module,
        "append_record",
        lambda _: pytest.fail("memory-derived repair must not be re-recorded"),
    )

    assert (
        cli_module._heal_file(
            test_file, "Error: waiting for locator('#old') timed out", [], dry_run=False
        ).is_success
        is True
    )


def test_cli_dry_run_restores_after_failure(
    mock_graph_failure, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error waiting for selector")
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file), "--dry-run"])
    assert result.exit_code == 1
    assert "not fixed after 3 loop(s)" in result.stderr
    assert test_file.read_text() == "await page.click('#old')"


def test_cli_exception_after_write_restores_original_file(
    mock_graph_crash_after_write, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_file = tmp_path / "test.spec.ts"
    original = "await page.click('#old')"
    test_file.write_text(original)
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error waiting for selector")
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file)])
    assert isinstance(result.exception, OSError)
    assert test_file.read_text() == original


def test_cli_heal_file_post_write_exception_propagates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The original exception must reach the _heal_file caller, not a rollback error."""
    test_file = tmp_path / "test.spec.ts"
    original = "await page.click('#old')"
    test_file.write_text(original)
    candidate = "await page.click('#new')"

    class MockGraph:
        def invoke(self, state: dict) -> dict:
            if state["test_script_path"]:
                atomic_write(Path(state["test_script_path"]), candidate)
            raise OSError("probe: post-write failure")

    monkeypatch.setattr(cli_module, "build_graph", lambda: MockGraph())
    with pytest.raises(OSError, match="probe: post-write failure"):
        cli_module._heal_file(test_file, "Timeout error waiting for selector", [], dry_run=False)
    assert test_file.read_text() == original


def test_cli_dry_run_restores_after_exception(
    mock_graph_crash_after_write, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_file = tmp_path / "test.spec.ts"
    original = "await page.click('#old')"
    test_file.write_text(original)
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error waiting for selector")
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file), "--dry-run"])
    assert isinstance(result.exception, OSError)
    assert test_file.read_text() == original


def test_cli_json_output(mock_graph_success, monkeypatch, tmp_path) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error waiting for selector")
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file), "--json"])
    assert result.exit_code == 0
    json_line = next(line for line in result.stdout.splitlines() if line.strip().startswith("{"))
    data = json.loads(json_line)
    assert data["is_success"] is True
    assert data["loop_count"] == 1
    assert len(data["instructions"]) == 1
    assert data["instructions"][0]["replacement"] == "await page.click('#new')"
    # Contract: single-file heal is a self-describing repair summary.
    assert data["kind"] == "repair"
    assert data["schema_version"] == SCHEMA_VERSION


def test_cli_suite_mode_emits_suite_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    suite = SuiteSummary(
        total_failed=1,
        healed=1,
        is_success=True,
        results=[RepairSummary(test_script_path="a.spec.ts", is_success=True, loop_count=1)],
    )
    monkeypatch.setattr(cli_module, "_heal_suite", lambda *a, **k: suite)
    monkeypatch.setattr(cli_module, "_read_diff", lambda *a, **k: "")
    monkeypatch.setattr(cli_module, "analyze_diff", lambda *a, **k: [])
    runner = CliRunner()
    result = runner.invoke(app, ["heal", "--json"])
    assert result.exit_code == 0
    json_line = next(line for line in result.stdout.splitlines() if line.strip().startswith("{"))
    data = json.loads(json_line)
    # Contract: suite mode emits a self-describing suite summary.
    assert data["kind"] == "suite"
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["results"][0]["kind"] == "repair"


def test_cli_diff_file_usage(mock_graph_success, monkeypatch, tmp_path) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error")
    diff_file = tmp_path / "my.diff"
    diff_file.write_text("dummy diff contents")
    called_diff_content = []

    def mock_analyze_diff(diff_content):
        called_diff_content.append(diff_content)
        return []

    monkeypatch.setattr(cli_module, "analyze_diff", mock_analyze_diff)
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file), "--diff", str(diff_file)])
    assert result.exit_code == 0
    assert called_diff_content == ["dummy diff contents"]


def test_cli_diff_base_usage(mock_graph_success, monkeypatch, tmp_path) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error")
    called_cmd = []

    def mock_run(cmd, **kwargs):
        called_cmd.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="git diff output", stderr=""
        )

    monkeypatch.setattr(cli_module.subprocess, "run", mock_run)
    runner = CliRunner()
    result = runner.invoke(
        app, [str(test_file), "--log", str(log_file), "--diff-base", "origin/main"]
    )
    assert result.exit_code == 0
    assert called_cmd == [["git", "diff", "origin/main...HEAD"]]


def test_cli_bad_diff_base_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error")

    def mock_run(cmd: list[str], **kwargs: object) -> NoReturn:
        raise subprocess.CalledProcessError(
            returncode=128, cmd=cmd, stderr="fatal: bad revision 'nope...HEAD'"
        )

    monkeypatch.setattr(cli_module.subprocess, "run", mock_run)
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file), "--diff-base", "nope"])
    assert result.exit_code == 2
    assert "Cannot read git diff" in _strip_ansi(result.stderr)
    assert "bad revision" in _strip_ansi(result.stderr)
    assert "Traceback" not in _strip_ansi(result.output)


def test_cli_git_not_found_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error")

    def mock_run(cmd: list[str], **kwargs: object) -> NoReturn:
        raise FileNotFoundError("git")

    monkeypatch.setattr(cli_module.subprocess, "run", mock_run)
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file)])
    assert result.exit_code == 2
    assert "git executable not found" in _strip_ansi(result.stderr)
    assert "Traceback" not in _strip_ansi(result.output)


def test_cli_sandbox_violation_exits_2(monkeypatch, tmp_path) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")

    def mock_assert_read_allowed(path):
        raise SandboxViolation("Denied read access to test path")

    monkeypatch.setattr(cli_module, "assert_read_allowed", mock_assert_read_allowed)
    runner = CliRunner()
    result = runner.invoke(app, [str(test_file)])
    assert result.exit_code == 2
    assert "sandbox denied:" in result.stderr


def test_cli_shadow_flag_runs_without_error() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--shadow"])
    assert result.exit_code == 0
    assert "Shadow Testing" in _strip_ansi(result.stderr)


def test_cli_help_documents_shadow_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--shadow" in _strip_ansi(result.stdout)


def test_cli_suite_passes(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "run_playwright", lambda path: (True, "Suite passes!"))
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "suite passes" in result.stderr


def test_cli_suite_failure_no_tests(monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "run_playwright", lambda path: (False, "Failure log"))
    monkeypatch.setattr(cli_module, "scan_failing_tests", lambda log: [])
    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "suite failed but no test files could be parsed/found" in result.stderr


def test_cli_suite_healing_success(mock_graph_success, monkeypatch, tmp_path) -> None:
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    # Auto-discovered targets must resolve under workspace_root (Issue #211).
    monkeypatch.setattr(cli_module.settings, "workspace_root", str(tmp_path))
    run_count = 0

    def mock_run_playwright(path):
        nonlocal run_count
        run_count += 1
        return (False, "Failure log")

    monkeypatch.setattr(cli_module, "run_playwright", mock_run_playwright)
    monkeypatch.setattr(cli_module, "scan_failing_tests", lambda log: [str(test_file)])
    runner = CliRunner()
    result = runner.invoke(app, [str(tmp_path)])
    assert result.exit_code == 0
    assert "1/1 test(s) healed" in result.stderr
    assert test_file.read_text() == "await page.click('#new')"
    assert run_count == 2


def test_cli_init_scaffolds_workflow_successfully(monkeypatch, tmp_path) -> None:
    # Change working directory to a temporary path so we don't overwrite your actual files
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr("app.cli.settings.llm_provider", "ollama")
    # Create a dummy playwright config so the readiness check exits with code 0
    (tmp_path / "playwright.config.ts").write_text("export default {}")
    runner = CliRunner()
    # Added "--scaffold" to trigger the workflow creation
    result = runner.invoke(app, ["init", "--scaffold"])
    assert result.exit_code == 0
    assert "Successfully scaffolded starter workflow" in result.stderr
    target_file = tmp_path / ".github" / "workflows" / "e2e-healer.yml"
    assert target_file.exists()
    assert "E2E Self-Healing CI" in target_file.read_text()


def test_cli_init_prevents_overwrite_unless_forced(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("app.cli.settings.llm_provider", "ollama")
    # Create a dummy playwright config so the readiness check exits with code 0 on success
    (tmp_path / "playwright.config.ts").write_text("export default {}")
    # Pre-create the file with dummy text
    target_dir = tmp_path / ".github" / "workflows"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "e2e-healer.yml"
    target_file.write_text("old content")
    runner = CliRunner()
    # 1. Try running without force flag -> Should fail and not change file
    # Added "--scaffold" to trigger the overwrite prevention logic
    result = runner.invoke(app, ["init", "--scaffold"])
    assert result.exit_code == 1
    assert "Workflow file already exists" in result.stderr
    assert target_file.read_text() == "old content"
    # 2. Try running with force flag -> Should succeed and overwrite file
    # Added "--scaffold" along with "--force"
    result_forced = runner.invoke(app, ["init", "--scaffold", "--force"])
    assert result_forced.exit_code == 0
    assert "Successfully scaffolded" in result_forced.stderr
    assert "E2E Self-Healing CI" in target_file.read_text()


# NEW TESTS: CLI boundary tests for notification paths (Issue #124)


def test_cli_heal_notifies_slack_single_file(monkeypatch, tmp_path) -> None:
    """Should notify Slack after healing a single file."""
    monkeypatch.chdir(tmp_path)

    # Mock the notification function and internal CLI dependencies
    with (
        patch("app.cli.notify_heal_outcome") as mock_notify,
        patch("app.cli.run_playwright", return_value=(False, "error log")),
        patch("app.cli._read_diff", return_value=""),
        patch("app.cli.analyze_diff", return_value=[]),
        patch("app.cli._heal_file") as mock_heal,
    ):
        mock_heal.return_value = RepairSummary(
            test_script_path=str(tmp_path / "test.spec.ts"),
            is_success=True,
            loop_count=1,
            instructions=[],
        )

        runner = CliRunner()
        test_file = tmp_path / "test.spec.ts"
        test_file.write_text("test('dummy', () => {})")

        runner.invoke(app, ["heal", str(test_file)])

        # Should have called notify_heal_outcome once
        assert mock_notify.call_count == 1
        call_args = mock_notify.call_args[0][0]
        assert isinstance(call_args, RepairSummary)


def test_cli_heal_notifies_slack_suite(monkeypatch, tmp_path) -> None:
    """Should notify Slack for each result in a suite heal."""
    monkeypatch.chdir(tmp_path)

    # Mock the notification function and internal CLI dependencies
    with (
        patch("app.cli.notify_heal_outcome") as mock_notify,
        patch("app.cli.run_playwright", return_value=(False, "error log")),
        patch("app.cli._read_diff", return_value=""),
        patch("app.cli.analyze_diff", return_value=[]),
        patch("app.cli.scan_failing_tests", return_value=["test.spec.ts"]),
        patch("app.cli._heal_file") as mock_heal,
    ):
        mock_heal.return_value = RepairSummary(
            test_script_path="test.spec.ts",
            is_success=True,
            loop_count=1,
            instructions=[],
        )

        runner = CliRunner()
        test_file = tmp_path / "test.spec.ts"
        test_file.write_text("test('dummy', () => {})")

        runner.invoke(app, ["heal"])

        # Should have called notify_heal_outcome for each result
        assert mock_notify.call_count >= 1
