"""Tests for concurrent selector verification (Issue #185)."""

import threading
from pathlib import Path
from unittest.mock import patch

from app.verify.selector_check import _get_helper_path, check_selectors


def test_get_helper_path_returns_unique_paths() -> None:
    """Each call to _get_helper_path should return a unique path."""
    path1 = _get_helper_path()
    path2 = _get_helper_path()

    # Paths should be different (unique IDs)
    assert path1 != path2

    # Both should be in cwd
    assert path1.parent == Path.cwd()
    assert path2.parent == Path.cwd()

    # Both should have the correct prefix and suffix
    assert path1.name.startswith(".e2e-healer-verify-")
    assert path1.name.endswith(".mjs")
    assert path2.name.startswith(".e2e-healer-verify-")
    assert path2.name.endswith(".mjs")


def test_concurrent_runs_use_unique_files(tmp_path: Path) -> None:
    """Two concurrent selector checks should use different helper files."""
    # Track which files were created
    created_files: list[Path] = []

    original_write = Path.write_text

    def tracking_write(self, content, *args, **kwargs):
        created_files.append(self)
        return original_write(self, content, *args, **kwargs)

    with patch.object(Path, "write_text", tracking_write):
        # Mock the subprocess to avoid actually running Node
        with patch("app.verify.selector_check.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"button": 1}'

            # Run two checks concurrently
            results = []
            threads = []

            def run_check():
                result = check_selectors("http://example.com", ["button"])
                results.append(result)

            for _ in range(2):
                t = threading.Thread(target=run_check)
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

    # Verify both succeeded
    assert len(results) == 2
    assert all(r == {"button": 1} for r in results)

    # Verify unique files were created
    assert len(created_files) == 2
    assert created_files[0] != created_files[1]
    assert all(f.suffix == ".mjs" for f in created_files)

    # Verify all files were cleaned up
    assert all(not f.exists() for f in created_files)


def test_helper_file_cleanup_on_permission_error(tmp_path: Path) -> None:
    """Cleanup should not fail even if unlink raises PermissionError (Windows)."""
    with patch("app.verify.selector_check.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = '{"div": 1}'

        # Mock Path.unlink to raise PermissionError (simulates Windows in-use file)
        with patch("pathlib.Path.unlink", side_effect=PermissionError("in use")):
            # Should not raise - cleanup failure is logged but not fatal
            result = check_selectors("http://example.com", ["div"])
            assert result == {"div": 1}
