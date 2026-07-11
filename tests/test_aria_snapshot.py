import time
from pathlib import Path

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


def test_extract_page_snapshot_missing():
    md = """
Some header info
# Page snapshot
No code block here
"""
    assert extract_page_snapshot(md) == ""
    assert extract_page_snapshot("") == ""


def test_extract_page_snapshot_none():
    assert extract_page_snapshot(None) == ""


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
    import os

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
