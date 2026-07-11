import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import app.cli as cli_module
from app.cli import app
from app.sandbox import SandboxViolation


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
            from app.utils.files import atomic_write

            if state["test_script_path"]:
                atomic_write(Path(state["test_script_path"]), state["current_code"])
            return state

    monkeypatch.setattr(cli_module, "build_graph", lambda: MockGraph())


@pytest.fixture
def mock_graph_failure(monkeypatch):
    class MockGraph:
        def invoke(self, state):
            state["is_success"] = False
            state["loop_count"] = 3
            state["current_code"] = "await page.click('#old')"
            state["patch_instructions"] = {}
            return state

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
):
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
    # review mode is advisory only — the test file must be untouched.
    assert test_file.read_text() == original


def test_cli_review_test_path_not_exists():
    runner = CliRunner()
    result = runner.invoke(app, ["review", "nonexistent_file.spec.ts"])
    assert result.exit_code == 2
    assert "path not found:" in result.stderr


def test_cli_test_path_not_exists():
    runner = CliRunner()
    result = runner.invoke(app, ["nonexistent_file.spec.ts"])
    assert result.exit_code == 2
    assert "path not found:" in result.stderr


def test_cli_single_file_already_passes(monkeypatch, tmp_path):
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#btn')")

    monkeypatch.setattr(cli_module, "run_playwright", lambda path: (True, "Passed!"))

    runner = CliRunner()
    result = runner.invoke(app, [str(test_file)])
    assert result.exit_code == 0
    assert "test already passes" in result.stderr


def test_cli_single_file_healed_success(mock_graph_success, monkeypatch, tmp_path):
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error waiting for selector")

    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file)])
    assert result.exit_code == 0
    assert "fixed after 1 loop(s)" in result.stderr
    assert test_file.read_text() == "await page.click('#new')"


def test_cli_single_file_healed_failed(mock_graph_failure, monkeypatch, tmp_path):
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error waiting for selector")

    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file)])
    assert result.exit_code == 1
    assert "not fixed after 3 loop(s)" in result.stderr
    assert test_file.read_text() == "await page.click('#old')"


def test_cli_dry_run_restores_file(mock_graph_success, monkeypatch, tmp_path):
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error waiting for selector")

    runner = CliRunner()
    result = runner.invoke(app, [str(test_file), "--log", str(log_file), "--dry-run"])
    assert result.exit_code == 0
    assert "fixed after 1 loop(s)" in result.stderr
    assert test_file.read_text() == "await page.click('#old')"


def test_cli_json_output(mock_graph_success, monkeypatch, tmp_path):
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


def test_cli_diff_file_usage(mock_graph_success, monkeypatch, tmp_path):
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


def test_cli_diff_base_usage(mock_graph_success, monkeypatch, tmp_path):
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")
    log_file = tmp_path / "error.log"
    log_file.write_text("Timeout error")

    called_cmd = []

    def mock_run(cmd, **kwargs):
        called_cmd.append(cmd)
        import subprocess

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


def test_cli_sandbox_violation_exits_2(monkeypatch, tmp_path):
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")

    def mock_assert_read_allowed(path):
        raise SandboxViolation("Denied read access to test path")

    monkeypatch.setattr(cli_module, "assert_read_allowed", mock_assert_read_allowed)

    runner = CliRunner()
    result = runner.invoke(app, [str(test_file)])
    assert result.exit_code == 2
    assert "sandbox denied:" in result.stderr


def test_cli_suite_passes(monkeypatch):
    monkeypatch.setattr(cli_module, "run_playwright", lambda path: (True, "Suite passes!"))

    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "suite passes" in result.stderr


def test_cli_suite_failure_no_tests(monkeypatch):
    monkeypatch.setattr(cli_module, "run_playwright", lambda path: (False, "Failure log"))
    monkeypatch.setattr(cli_module, "scan_failing_tests", lambda log: [])

    runner = CliRunner()
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "suite failed but no test files could be parsed/found" in result.stderr


def test_cli_suite_healing_success(mock_graph_success, monkeypatch, tmp_path):
    test_file = tmp_path / "test.spec.ts"
    test_file.write_text("await page.click('#old')")

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
