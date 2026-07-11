import subprocess

import pytest

from app.config import settings
from app.runner import run_playwright
from app.sandbox import SandboxViolation


def test_run_playwright_success(monkeypatch):
    called = []

    def mock_run(cmd, **kwargs):
        called.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="Success stdout\n",
            stderr="Success stderr\n",
        )

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(settings, "playwright_cmd", "npx playwright test")

    passed, log = run_playwright("tests/login.spec.ts")

    assert passed is True
    assert log == "Success stdout\nSuccess stderr\n"
    assert called == [["npx", "playwright", "test", "tests/login.spec.ts"]]


def test_run_playwright_failure(monkeypatch):
    called = []

    def mock_run(cmd, **kwargs):
        called.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout="Failure stdout\n",
            stderr="Failure stderr\n",
        )

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(settings, "playwright_cmd", "npx playwright test")

    passed, log = run_playwright()

    assert passed is False
    assert log == "Failure stdout\nFailure stderr\n"
    assert called == [["npx", "playwright", "test"]]


def test_run_playwright_sandbox_violation(monkeypatch):
    monkeypatch.setattr(settings, "playwright_cmd", "npx playwright test && rm -rf")
    called = False

    def mock_run(cmd, **kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(SandboxViolation):
        run_playwright("tests/login.spec.ts")

    assert not called
