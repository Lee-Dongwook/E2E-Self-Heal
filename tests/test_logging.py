import io
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog
from pydantic import SecretStr
from typer.testing import CliRunner

import app.cli as cli_module
import app.config as config_module
from app.cli import app
from app.logging import configure_logging
from app.safety_benchmark import ScenarioRestoreError

SECRET = "sk-live-8c7b6a5f4e3d"


@pytest.fixture
def restore_structlog_config() -> Iterator[None]:
    """configure_logging binds the current sys.stderr; put the global config back afterwards."""
    saved = structlog.get_config()
    yield
    structlog.configure(**saved)


def test_log_output_goes_to_stderr_never_stdout(
    capsys: pytest.CaptureFixture[str], restore_structlog_config: None
) -> None:
    # `--json` writes the result to stdout and CI redirects it into a file, so a single log
    # line on stdout would make that file invalid JSON.
    configure_logging("INFO")

    structlog.get_logger("probe").info("probe_event", answer=42)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "probe_event" in captured.err


def test_a_cached_logger_follows_sys_stderr_after_it_is_replaced(
    monkeypatch: pytest.MonkeyPatch, restore_structlog_config: None
) -> None:
    # Loggers are cached on first use. If the stream were bound when logging is configured,
    # replacing sys.stderr later (as a CLI test runner does) would leave a cached logger
    # writing to a stale, possibly closed, stream.
    first = io.StringIO()
    monkeypatch.setattr(sys, "stderr", first)
    configure_logging("INFO")
    logger = structlog.get_logger("cached_probe")
    logger.info("first_event")
    first.close()

    second = io.StringIO()
    monkeypatch.setattr(sys, "stderr", second)
    logger.info("second_event")

    assert "second_event" in second.getvalue()


# --- redaction boundary (#377) ------------------------------------------------------------


def _configure_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name a secret through configuration, as a user would."""
    monkeypatch.setenv("E2E_TEST_PROVIDER_KEY", SECRET)
    monkeypatch.setattr(config_module.settings, "sensitive_env_vars", "E2E_TEST_PROVIDER_KEY")
    monkeypatch.setattr(config_module.settings, "sensitive_values", SecretStr(""))


def test_a_configured_secret_in_a_log_value_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restore_structlog_config: None,
) -> None:
    # Logs are the other exit for the evidence that reaches a prompt: a provider failure or a
    # patch rejection quotes the text being repaired.
    _configure_secret(monkeypatch)
    configure_logging("INFO")

    structlog.get_logger("redaction_probe").warning(
        "provider_failed", error=f"request rejected for key {SECRET}"
    )

    rendered = capsys.readouterr().err
    assert SECRET not in rendered
    assert "[REDACTED]" in rendered
    assert "provider_failed" in rendered


def test_a_configured_secret_in_a_traceback_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restore_structlog_config: None,
) -> None:
    # The processor runs after format_exc_info, so exception text is covered too.
    _configure_secret(monkeypatch)
    configure_logging("INFO")

    try:
        raise RuntimeError(f"upstream rejected {SECRET}")
    except RuntimeError:
        structlog.get_logger("redaction_probe").exception("call_failed")

    rendered = capsys.readouterr().err
    assert SECRET not in rendered
    assert "RuntimeError" in rendered


def test_builtin_credential_patterns_are_redacted_without_configuration(
    capsys: pytest.CaptureFixture[str], restore_structlog_config: None
) -> None:
    configure_logging("INFO")

    structlog.get_logger("redaction_probe").info(
        "patch_rejected", detail="authorization: bearer plain-token-value"
    )

    rendered = capsys.readouterr().err
    assert "plain-token-value" not in rendered
    assert "[REDACTED]" in rendered


def test_ordinary_log_values_are_left_alone(
    capsys: pytest.CaptureFixture[str], restore_structlog_config: None
) -> None:
    configure_logging("INFO")

    structlog.get_logger("redaction_probe").info("diagnoser_finished", loop_count=2)

    rendered = capsys.readouterr().err
    assert "diagnoser_finished" in rendered
    assert "loop_count" in rendered
    assert "[REDACTED]" not in rendered


def test_a_safety_benchmark_provider_failure_prints_no_key_and_no_locals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, restore_structlog_config: None
) -> None:
    # A sequential safety benchmark logs in the CLI process itself (#413). Start from
    # structlog's defaults, as a fresh `e2e-healer safety-benchmark` process does: those render
    # every frame's locals in a traceback and never redact.
    structlog.reset_defaults()
    _configure_secret(monkeypatch)
    unconfigured_local = "local-value-0123456789"

    def failing_run(*_args: Any, **_kwargs: Any) -> None:
        request_headers = {"authorization": unconfigured_local}  # noqa: F841 - a frame local
        try:
            raise RuntimeError(f"upstream rejected {SECRET}")
        except RuntimeError:
            structlog.get_logger("safety_probe").exception("diagnosis_failed")
        raise ScenarioRestoreError("stop the run here")

    monkeypatch.setattr(cli_module, "discover_safety_scenarios", lambda _root: [])
    monkeypatch.setattr(cli_module, "assert_scenarios_writable", lambda _scenarios: None)
    monkeypatch.setattr(cli_module, "run_safety_benchmark", failing_run)

    result = CliRunner().invoke(app, ["safety-benchmark", "--scenario-root", str(tmp_path)])

    assert result.exit_code == 1
    rendered = result.stdout + result.stderr
    assert "diagnosis_failed" in result.stderr
    assert SECRET not in rendered
    assert unconfigured_local not in rendered
