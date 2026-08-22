"""Suite-mode orchestration, with Playwright and per-file healing mocked out."""

import pytest
from pathlib import Path

import app.cli as cli
from app.config import settings
from app.schemas import RepairSummary


def _combined(*paths) -> str:
    return "".join(f"  {i + 1}) {p}:1:1 › t\n" for i, p in enumerate(paths))


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
    combined = _combined(a, b)

    def fake_run(target=""):
        return (False, combined) if target == "" else (False, "focused")

    monkeypatch.setattr(cli, "run_playwright", fake_run)

    def _heal(
        path: Path, log: str, context: list[dict], dry_run: bool, memory: bool
    ) -> RepairSummary:
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    summary = cli._heal_suite("", [], dry_run=False)
    assert (summary.total_failed, summary.healed, summary.is_success) == (2, 2, True)


def test_suite_partial_heal_is_not_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    a, b = tmp_path / "a.spec.ts", tmp_path / "b.spec.ts"
    a.write_text("x")
    b.write_text("y")
    combined = _combined(a, b)
    monkeypatch.setattr(
        cli, "run_playwright", lambda target="": (False, combined) if target == "" else (False, "f")
    )

    def _heal(
        path: Path, log: str, context: list[dict], dry_run: bool, memory: bool
    ) -> RepairSummary:
        return RepairSummary(
            test_script_path=str(path), is_success=(path.name == "a.spec.ts"), loop_count=1
        )

    monkeypatch.setattr(cli, "_heal_file", _heal)
    summary = cli._heal_suite("", [], dry_run=False)
    assert (summary.total_failed, summary.healed, summary.is_success) == (2, 1, False)


def test_suite_skips_heal_when_file_passes_on_rerun(monkeypatch, tmp_path):
    a = tmp_path / "a.spec.ts"
    a.write_text("x")
    combined = _combined(a)

    def fake_run(target=""):
        return (False, combined) if target == "" else (True, "")  # rerun passes

    def _must_not_heal(*args, **kwargs):
        raise AssertionError("_heal_file should not run when the rerun passes")

    monkeypatch.setattr(cli, "run_playwright", fake_run)
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
    combined = _combined(outside, inside)

    def fake_run(target=""):
        return (False, combined) if target == "" else (False, "focused")

    def _heal(
        path: Path, log: str, context: list[dict], dry_run: bool, memory: bool
    ) -> RepairSummary:
        assert path == inside, "only the in-workspace target may be healed"
        assert memory is True
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "run_playwright", fake_run)
    monkeypatch.setattr(cli, "_heal_file", _heal)
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
    combined = _combined("../victim.spec.ts", inside)

    def fake_run(target=""):
        return (False, combined) if target == "" else (False, "focused")

    def _heal(
        path: Path, log: str, context: list[dict], dry_run: bool, memory: bool
    ) -> RepairSummary:
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "run_playwright", fake_run)
    monkeypatch.setattr(cli, "_heal_file", _heal)
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

    def fake_run(target=""):
        return (False, _combined(test_file)) if target == "" else (False, "focused")

    def _heal(
        path: Path, log: str, context: list[dict], dry_run: bool, memory: bool
    ) -> RepairSummary:
        seen_memory.append(memory)
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=0)

    monkeypatch.setattr(cli, "run_playwright", fake_run)
    monkeypatch.setattr(cli, "_heal_file", _heal)

    summary = cli._heal_suite("", [], dry_run=False, memory=False)

    assert summary.is_success is True
    assert seen_memory == [False]
