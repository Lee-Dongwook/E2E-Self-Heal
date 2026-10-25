import json
import subprocess
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.verify.selector_check import check_selectors


def test_check_selectors_empty():
    assert check_selectors("http://example.com", []) == {}


def test_check_selectors_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        mock_output = {"#btn": 1, "#missing": 0, ".header": 2, "#bad": -1}
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps(mock_output),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = check_selectors("http://example.com", ["#btn", "#missing", ".header", "#bad"])

    assert result == {"#btn": 1, "#missing": 0, ".header": 2, "#bad": -1}


@pytest.mark.parametrize(
    "outcome",
    ["nonzero-exit", "malformed-json", "subprocess-error"],
    ids=["nonzero-exit", "malformed-json", "subprocess-error"],
)
def test_check_selectors_gracefully_skips_failures(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    def mock_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if outcome == "subprocess-error":
            raise FileNotFoundError("node")
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1 if outcome == "nonzero-exit" else 0,
            stdout="" if outcome == "nonzero-exit" else "not-a-json-string",
            stderr="Node process failed",
        )

    monkeypatch.setattr(subprocess, "run", mock_run)

    assert check_selectors("http://example.com", ["#btn"]) is None


def test_concurrent_runs_use_unique_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    created_files: list[Path] = []
    original_write = Path.write_text

    def tracking_write(self: Path, content: str, *args: Any, **kwargs: Any) -> int:
        created_files.append(self)
        return original_write(self, content, *args, **kwargs)

    with patch.object(Path, "write_text", tracking_write):
        with patch("app.verify.selector_check.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"button": 1}'
            results: list[dict[str, int] | None] = []

            def run_check() -> None:
                results.append(check_selectors("http://example.com", ["button"]))

            threads = [threading.Thread(target=run_check) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

    assert results == [{"button": 1}, {"button": 1}]
    assert len(created_files) == 2
    assert created_files[0] != created_files[1]
    assert all(file.suffix == ".mjs" for file in created_files)
    assert all(not file.exists() for file in created_files)


def test_helper_file_cleanup_survives_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with patch("app.verify.selector_check.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = '{"div": 1}'
        with patch("pathlib.Path.unlink", side_effect=PermissionError("in use")):
            assert check_selectors("http://example.com", ["div"]) == {"div": 1}
