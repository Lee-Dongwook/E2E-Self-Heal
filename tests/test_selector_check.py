import json
import subprocess

from app.verify.selector_check import check_selectors


def test_check_selectors_empty():
    assert check_selectors("http://example.com", []) == {}


def test_check_selectors_success(monkeypatch):
    called = []

    def mock_run(cmd, **kwargs):
        called.append(cmd)
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
    assert len(called) == 1
    assert called[0][0] == "node"
    assert called[0][2] == "http://example.com"
    assert json.loads(called[0][3]) == ["#btn", "#missing", ".header", "#bad"]


def test_check_selectors_graceful_skip_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda x: None)

    def mock_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout="",
            stderr="Node process failed",
        )

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = check_selectors("http://example.com", ["#btn"])
    assert result is None


def test_check_selectors_graceful_skip_on_malformed_json(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda x: None)

    def mock_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="not-a-json-string",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = check_selectors("http://example.com", ["#btn"])
    assert result is None


def test_check_selectors_graceful_skip_on_subprocess_exception(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda x: None)

    def mock_run(cmd, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'node'")

    monkeypatch.setattr(subprocess, "run", mock_run)

    result = check_selectors("http://example.com", ["#btn"])
    assert result is None
