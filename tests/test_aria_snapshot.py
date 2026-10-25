import os
import time
from pathlib import Path

import pytest

import app.preprocess.aria_snapshot as aria_snapshot_module
from app.preprocess.aria_snapshot import extract_page_snapshot, read_failure_snapshot
from app.sandbox import SandboxViolation


def test_extract_page_snapshot_valid():
    md = """
Some header info
# Page snapshot
```yaml
- role: heading
  name: Welcome
- role: button
  name: Click me
```
Some footer info
"""
    expected = "- role: heading\n  name: Welcome\n- role: button\n  name: Click me"
    assert extract_page_snapshot(md) == expected


@pytest.mark.parametrize(
    "markdown",
    [
        """
        Some header info
        # Page snapshot
        No code block here
        """,
        "",
        None,
    ],
    ids=["missing-code-block", "empty", "none"],
)
def test_extract_page_snapshot_missing(markdown: str | None) -> None:
    assert extract_page_snapshot(markdown) == ""


def test_read_failure_snapshot_missing_dir():
    assert read_failure_snapshot(Path("does/not/exist")) == ""


def test_read_failure_snapshot_newest_file(tmp_path):
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()

    dir_a = results_dir / "test-a"
    dir_b = results_dir / "test-b"
    dir_a.mkdir()
    dir_b.mkdir()

    file_a = dir_a / "first-error-context.md"
    file_b = dir_b / "second-error-context.md"

    snapshot_a = "# Page snapshot\n```yaml\n- role: link\n  name: A\n```"
    snapshot_b = "# Page snapshot\n```yaml\n- role: link\n  name: B\n```"

    file_a.write_text(snapshot_a)
    file_b.write_text(snapshot_b)

    file_b_time = time.time() - 100
    file_a_time = time.time()
    file_b.touch()
    file_a.touch()

    os.utime(file_b, (file_b_time, file_b_time))
    os.utime(file_a, (file_a_time, file_a_time))

    assert read_failure_snapshot(results_dir) == "- role: link\n  name: A"

    file_b_time_newer = time.time() + 100
    os.utime(file_b, (file_b_time_newer, file_b_time_newer))

    assert read_failure_snapshot(results_dir) == "- role: link\n  name: B"


def test_read_failure_snapshot_sandbox_violation_on_dir(tmp_path, monkeypatch):
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()

    def mock_assert_read_allowed(path):
        if path == results_dir:
            raise SandboxViolation("Sandbox denied read on results directory")

    monkeypatch.setattr(aria_snapshot_module, "assert_read_allowed", mock_assert_read_allowed)

    assert read_failure_snapshot(results_dir) == ""


def test_read_failure_snapshot_sandbox_violation_on_file(tmp_path, monkeypatch):
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    file_a = results_dir / "error-context.md"
    file_a.write_text("# Page snapshot\n```yaml\n- role: link\n```")

    def mock_assert_read_allowed(path):
        if path == file_a:
            raise SandboxViolation("Sandbox denied read on file")

    monkeypatch.setattr(aria_snapshot_module, "assert_read_allowed", mock_assert_read_allowed)

    assert read_failure_snapshot(results_dir) == ""


def test_read_failure_snapshot_filters_by_test_path(tmp_path: Path) -> None:
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()

    test_a_dir = results_dir / "spec-test-a"
    test_a_dir.mkdir()
    (test_a_dir / "error-context.md").write_text(
        "# Page snapshot\n```yaml\n- button: Submit A\n```"
    )

    test_b_dir = results_dir / "spec-test-b"
    test_b_dir.mkdir()
    (test_b_dir / "error-context.md").write_text(
        "# Page snapshot\n```yaml\n- button: Submit B\n```"
    )

    snapshot = read_failure_snapshot(results_dir, tmp_path / "tests" / "spec-test-a.ts")
    assert "Submit A" in snapshot
    assert "Submit B" not in snapshot


def test_read_failure_snapshot_no_match_returns_empty(tmp_path: Path) -> None:
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()

    other_dir = results_dir / "spec-other"
    other_dir.mkdir()
    (other_dir / "error-context.md").write_text("# Page snapshot\n```yaml\n- button: Other\n```")

    snapshot = read_failure_snapshot(results_dir, tmp_path / "tests" / "spec-missing.ts")
    assert snapshot == ""


def test_read_failure_snapshot_handles_toctou(tmp_path: Path) -> None:
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()

    test_dir = results_dir / "spec-test"
    test_dir.mkdir()
    context_file = test_dir / "error-context.md"
    context_file.write_text("# Page snapshot\n```yaml\n- button: Test\n```")
    context_file.unlink()

    snapshot = read_failure_snapshot(results_dir, tmp_path / "tests" / "spec-test.ts")
    assert snapshot == ""


def test_read_failure_snapshot_ambiguous_match_picks_newest(tmp_path: Path) -> None:
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()

    dir1 = results_dir / "spec-run-1"
    dir1.mkdir()
    (dir1 / "error-context.md").write_text("# Page snapshot\n```yaml\n- button: Run 1\n```")

    dir2 = results_dir / "spec-run-2"
    dir2.mkdir()
    file2 = dir2 / "error-context.md"
    file2.write_text("# Page snapshot\n```yaml\n- button: Run 2\n```")
    time.sleep(0.01)
    file2.write_text("# Page snapshot\n```yaml\n- button: Run 2\n```")

    snapshot = read_failure_snapshot(results_dir, tmp_path / "tests" / "spec.ts")
    assert "Run 2" in snapshot


def test_read_failure_snapshot_matches_truncated_directory_using_location(
    tmp_path: Path,
) -> None:
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()

    target_dir = results_dir / "catalog-pagination-regress-69aaf--to-the-second-catalog-page"
    target_dir.mkdir()
    (target_dir / "error-context.md").write_text(
        "# Test info\n"
        "- Name: catalog-pagination-regression/spec.ts >> advances to the second catalog page\n"
        "- Location: scenarios/catalog-pagination-regression/spec.ts:3:1\n\n"
        "# Page snapshot\n```yaml\n- button: Target\n```"
    )

    wrong_dir = results_dir / "spec-other-catalog-pagination-regress"
    wrong_dir.mkdir()
    (wrong_dir / "error-context.md").write_text(
        "# Test info\n"
        "- Name: catalog-pagination-other/spec.ts >> advances to the second catalog page\n"
        "- Location: scenarios/catalog-pagination-other/spec.ts:3:1\n\n"
        "# Page snapshot\n```yaml\n- button: Wrong test\n```"
    )

    snapshot = read_failure_snapshot(
        results_dir, Path("scenarios/catalog-pagination-regression/spec.ts")
    )
    assert "Target" in snapshot
    assert "Wrong test" not in snapshot
