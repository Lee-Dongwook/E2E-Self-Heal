import subprocess

import pytest

from app.config import settings
from app.runner import run_playwright
from app.sandbox import SandboxViolation


class FakePopen:
    """Minimal ``subprocess.Popen`` stand-in driven by scripted communicate() results."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        output: tuple[str, str] = ("", ""),
        timeout_first: bool = False,
    ) -> None:
        self.pid = 4242
        self.returncode = returncode
        self._output = output
        self._timeout_first = timeout_first
        self.communicate_calls = 0
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.communicate_calls += 1
        if self._timeout_first and self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(
                cmd="npx",
                timeout=timeout or 0,
                output="partial stdout\n",
                stderr="partial stderr\n",
            )
        return self._output

    def poll(self) -> int | None:
        return None if not self.killed else -9

    def kill(self) -> None:
        self.killed = True


def test_run_playwright_success(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple[list[str], dict[str, object]]] = []

    def mock_popen(cmd: list[str], **kwargs: object) -> FakePopen:
        called.append((cmd, kwargs))
        return FakePopen(returncode=0, output=("Success stdout\n", "Success stderr\n"))

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr(settings, "playwright_cmd", "npx playwright test")

    passed, log = run_playwright("tests/login.spec.ts")

    assert passed is True
    assert log == "Success stdout\nSuccess stderr\n"
    # Child must run in its own process group / session so timeouts can reap the tree.
    kwargs = called[0][1]
    assert kwargs.get("start_new_session") or "creationflags" in kwargs


def test_run_playwright_failure(monkeypatch: pytest.MonkeyPatch) -> None:

    def mock_popen(cmd: list[str], **kwargs: object) -> FakePopen:
        return FakePopen(returncode=1, output=("Failure stdout\n", "Failure stderr\n"))

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr(settings, "playwright_cmd", "npx playwright test")

    passed, log = run_playwright()

    assert passed is False
    assert log == "Failure stdout\nFailure stderr\n"


def test_run_playwright_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    timeouts: list[float | None] = []
    fake = FakePopen(output=("", ""), timeout_first=True)

    def mock_popen(cmd: list[str], **kwargs: object) -> FakePopen:
        return fake

    def fake_terminate(process: FakePopen) -> None:
        process.killed = True

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    monkeypatch.setattr("app.runner._terminate_process_tree", fake_terminate)
    monkeypatch.setattr(settings, "playwright_cmd", "npx playwright test")
    monkeypatch.setattr(settings, "test_timeout_seconds", 5)

    original_communicate = fake.communicate

    def tracking_communicate(timeout: float | None = None) -> tuple[str, str]:
        timeouts.append(timeout)
        return original_communicate(timeout=timeout)

    fake.communicate = tracking_communicate

    passed, log = run_playwright("tests/login.spec.ts")

    assert passed is False
    assert timeouts[0] == 5
    assert fake.killed is True
    assert "partial stdout" in log
    assert "partial stderr" in log
    assert "timed out after 5s" in log


def test_run_playwright_sandbox_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "playwright_cmd", "npx playwright test && rm -rf")
    called = False

    def mock_popen(cmd: list[str], **kwargs: object) -> FakePopen:
        nonlocal called
        called = True
        return FakePopen(returncode=0, output=("", ""))

    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    with pytest.raises(SandboxViolation):
        run_playwright("tests/login.spec.ts")

    assert not called
