"""Suite-mode orchestration, with Playwright and per-file healing mocked out."""

from collections.abc import Callable
from pathlib import Path

import pytest

import app.cli as cli
from app.config import settings
from app.schemas import RepairSummary

type FailureTarget = Path | str
type PlaywrightResult = tuple[bool, str]
type FocusedRunner = Callable[[str], PlaywrightResult]


def _combined(*paths: FailureTarget) -> str:
    return "".join(f"  {i + 1}) {p}:1:1 › t\n" for i, p in enumerate(paths))


def _suite_runner(
    initial_failures: tuple[FailureTarget, ...],
    focused: FocusedRunner,
    final_passed: bool,
    final_failures: tuple[FailureTarget, ...] = (),
) -> FocusedRunner:
    """Fake initial suite, focused reruns, and final verification."""
    suite_calls = {"n": 0}

    def fake(target: str = "") -> PlaywrightResult:
        if target != "":
            return focused(target)
        suite_calls["n"] += 1
        if suite_calls["n"] == 1:
            return (False, _combined(*initial_failures))
        if final_passed:
            return (True, "")
        return (False, _combined(*final_failures))

    return fake


@pytest.fixture(autouse=True)
def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Auto-discovered targets must resolve under workspace_root, so anchor it to tmp_path
    # where the fixtures live (Issue #211).
    monkeypatch.setattr(settings, "sandbox_mode", "relaxed")
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))


def test_suite_passes_nothing_to_heal(monkeypatch):
    monkeypatch.setattr(cli, "run_playwright", lambda target="": (True, ""))
    summary = cli._heal_suite("", [], dry_run=False)
    assert summary.total_failed == 0
    assert summary.is_success is True


def test_suite_all_healed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    a, b = tmp_path / "a.spec.ts", tmp_path / "b.spec.ts"
    a.write_text("x")
    b.write_text("y")

    def _heal(
        path: Path, log: str, context: list[dict], dry_run: bool, memory_enabled: bool
    ) -> RepairSummary:
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    monkeypatch.setattr(
        cli, "run_playwright", _suite_runner((a, b), lambda t: (False, "focused"), True)
    )
    summary = cli._heal_suite("", [], dry_run=False)
    assert (summary.total_failed, summary.healed, summary.is_success) == (2, 2, True)


def test_suite_partial_heal_is_not_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    a, b = tmp_path / "a.spec.ts", tmp_path / "b.spec.ts"
    a.write_text("x")
    b.write_text("y")

    def _heal(
        path: Path, log: str, context: list[dict], dry_run: bool, memory_enabled: bool
    ) -> RepairSummary:
        return RepairSummary(
            test_script_path=str(path), is_success=(path.name == "a.spec.ts"), loop_count=1
        )

    monkeypatch.setattr(cli, "_heal_file", _heal)
    # `a` heals; final verification still fails on `b`.
    monkeypatch.setattr(
        cli, "run_playwright", _suite_runner((a, b), lambda t: (False, "f"), False, (b,))
    )
    summary = cli._heal_suite("", [], dry_run=False)
    assert (summary.total_failed, summary.healed, summary.is_success) == (2, 1, False)


def test_suite_skips_heal_when_file_passes_on_rerun(monkeypatch, tmp_path):
    a = tmp_path / "a.spec.ts"
    a.write_text("x")

    def _must_not_heal(*args, **kwargs):
        raise AssertionError("_heal_file should not run when the rerun passes")

    # The focused and final reruns pass.
    monkeypatch.setattr(cli, "run_playwright", _suite_runner((a,), lambda t: (True, ""), True))
    monkeypatch.setattr(cli, "_heal_file", _must_not_heal)
    summary = cli._heal_suite("", [], dry_run=False)
    assert (summary.total_failed, summary.healed, summary.is_success) == (1, 1, True)


def test_suite_denies_external_path_but_keeps_it_visible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An absolute path outside the workspace (attacker-influenced reporter output) must not
    # be patched, yet must stay visible as an unresolved suite result (Issue #211).
    inside = tmp_path / "a.spec.ts"
    inside.write_text("x")
    outside = tmp_path.parent / "victim.spec.ts"
    outside.write_text("secret")

    def _heal(
        path: Path, log: str, context: list[dict], dry_run: bool, memory_enabled: bool
    ) -> RepairSummary:
        assert path == inside, "only the in-workspace target may be healed"
        assert memory_enabled is True
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    # The denied path remains unresolved; the in-workspace file heals.
    monkeypatch.setattr(
        cli,
        "run_playwright",
        _suite_runner((outside, inside), lambda t: (False, "focused"), False, (outside,)),
    )
    summary = cli._heal_suite("", [], dry_run=False)

    # Both failures are reported; the external one is unresolved, so the suite is not success.
    assert (summary.total_failed, summary.healed, summary.is_success) == (2, 1, False)
    denied = next(r for r in summary.results if r.test_script_path == str(outside))
    assert denied.is_success is False
    assert outside.read_text() == "secret"


def test_suite_denies_relative_external_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A relative path that resolves outside the workspace is rejected too.
    inside = tmp_path / "a.spec.ts"
    inside.write_text("x")

    def _heal(
        path: Path, log: str, context: list[dict], dry_run: bool, memory: bool
    ) -> RepairSummary:
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    monkeypatch.setattr(
        cli,
        "run_playwright",
        _suite_runner(
            ("../victim.spec.ts", inside),
            lambda t: (False, "focused"),
            False,
            ("../victim.spec.ts",),
        ),
    )
    summary = cli._heal_suite("", [], dry_run=False)
    assert (summary.total_failed, summary.healed, summary.is_success) == (2, 1, False)
    assert any(
        r.test_script_path == "../victim.spec.ts" and not r.is_success for r in summary.results
    )


def test_suite_threads_no_memory_to_each_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_file = tmp_path / "a.spec.ts"
    test_file.write_text("x")
    seen_memory: list[bool] = []

    def _heal(
        path: Path, log: str, context: list[dict], dry_run: bool, memory_enabled: bool
    ) -> RepairSummary:
        seen_memory.append(memory_enabled)
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=0)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    monkeypatch.setattr(
        cli, "run_playwright", _suite_runner((test_file,), lambda t: (False, "focused"), True)
    )

    summary = cli._heal_suite("", [], dry_run=False, memory_enabled=False)

    assert summary.is_success is True
    assert seen_memory == [False]


def test_suite_keeps_missing_file_visible(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "gone.spec.ts"
    monkeypatch.setattr(
        cli,
        "run_playwright",
        _suite_runner((missing,), lambda t: (False, "focused"), False, (missing,)),
    )
    summary = cli._heal_suite("", [], dry_run=False)
    assert summary.total_failed == 1
    assert summary.healed == 0
    assert summary.is_success is False
    assert any(r.test_script_path == str(missing) and not r.is_success for r in summary.results)


def test_suite_final_rerun_reveals_new_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `a` heals; final verification reveals new failure `b`.
    a, b = tmp_path / "a.spec.ts", tmp_path / "b.spec.ts"
    a.write_text("x")
    b.write_text("y")

    def _heal(
        path: Path,
        log: str,
        context: list[dict],
        dry_run: bool,
        memory_enabled: bool,
    ) -> RepairSummary:
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    monkeypatch.setattr(
        cli, "run_playwright", _suite_runner((a,), lambda t: (False, "focused"), False, (b,))
    )
    summary = cli._heal_suite("", [], dry_run=False)
    assert summary.total_failed == 2
    assert summary.healed == 1
    assert summary.is_success is False
    assert any(r.test_script_path == str(b) and not r.is_success for r in summary.results)


def test_suite_unparseable_final_failure_marks_all_unresolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An unparseable final failure invalidates all repairs.
    a = tmp_path / "a.spec.ts"
    a.write_text("x")

    def _heal(
        path: Path,
        log: str,
        context: list[dict],
        dry_run: bool,
        memory_enabled: bool,
    ) -> RepairSummary:
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    monkeypatch.setattr(
        cli, "run_playwright", _suite_runner((a,), lambda t: (False, "focused"), False, ())
    )
    summary = cli._heal_suite("", [], dry_run=False)
    assert summary.total_failed == 1
    assert summary.healed == 0
    assert summary.is_success is False
    assert all(not r.is_success for r in summary.results)


def test_suite_dry_run_reports_non_successful_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Dry runs have no final verification.
    a = tmp_path / "a.spec.ts"
    a.write_text("x")

    def _heal(
        path: Path,
        log: str,
        context: list[dict],
        dry_run: bool,
        memory_enabled: bool,
    ) -> RepairSummary:
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    monkeypatch.setattr(
        cli, "run_playwright", _suite_runner((a,), lambda t: (False, "focused"), True)
    )
    summary = cli._heal_suite("", [], dry_run=True)
    assert summary.total_failed == 1
    assert summary.healed == 1
    assert summary.is_success is False


def test_suite_final_rerun_reveals_failures_in_scanner_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Append new final failures in scanner order.
    a = tmp_path / "a.spec.ts"
    a.write_text("x")
    b = tmp_path / "b.spec.ts"
    b.write_text("y")
    c = tmp_path / "c.spec.ts"
    c.write_text("z")

    def _heal(
        path: Path,
        log: str,
        context: list[dict],
        dry_run: bool,
        memory_enabled: bool,
    ) -> RepairSummary:
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    monkeypatch.setattr(
        cli, "run_playwright", _suite_runner((a,), lambda t: (False, "focused"), False, (b, c))
    )
    summary = cli._heal_suite("", [], dry_run=False)
    revealed = [r.test_script_path for r in summary.results if not r.is_success]
    assert revealed == [str(b), str(c)]
