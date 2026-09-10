"""Suite-mode orchestration, with Playwright and per-file healing mocked out."""

import pytest
from pathlib import Path

import app.cli as cli
from app.config import settings
from app.schemas import RepairSummary


def _combined(*paths) -> str:
    return "".join(f"  {i + 1}) {p}:1:1 › t\n" for i, p in enumerate(paths))


def _suite_runner(initial_failures, focused, final_passed, final_failures=()):
    """Stateful ``run_playwright`` fake: initial fail -> focused reruns -> final full-suite.

    ``focused(target)`` returns ``(passed, log)`` for a per-file rerun. The first
    ``suite_target`` call returns the initial failure log; the second returns the final
    verification result — pass, or the still-failing tests (Issue #212).
    """
    suite_calls = {"n": 0}

    def fake(target=""):
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
    # a heals, b does not; the final full-suite rerun still fails on b.
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

    # Focused rerun passes, so no heal; the final full-suite rerun also passes.
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
    # The denied external path still fails in the final rerun; the in-workspace file heals.
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


def test_suite_keeps_missing_file_visible(monkeypatch, tmp_path):
    # A failing test whose file no longer exists must remain in the summary (Issue #212).
    missing = tmp_path / "gone.spec.ts"  # intentionally not created
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


def test_suite_final_rerun_reveals_new_failure(monkeypatch, tmp_path):
    # A focused fix to `a` regresses `b` (which passed the initial run): the final
    # full-suite rerun must catch it and surface `b` as unresolved (Issue #212).
    a, b = tmp_path / "a.spec.ts", tmp_path / "b.spec.ts"
    a.write_text("x")
    b.write_text("y")

    def _heal(path, log, context, dry_run, memory_enabled):
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    # Initial run fails only on `a`; `a` heals; the final rerun fails on `b` (new).
    monkeypatch.setattr(
        cli, "run_playwright", _suite_runner((a,), lambda t: (False, "focused"), False, (b,))
    )
    summary = cli._heal_suite("", [], dry_run=False)
    assert summary.total_failed == 2  # a (initial) + b (revealed by final rerun)
    assert summary.healed == 1  # a genuinely healed; b is a new unresolved failure
    assert summary.is_success is False
    assert any(r.test_script_path == str(b) and not r.is_success for r in summary.results)


def test_suite_unparseable_final_failure_marks_all_unresolved(monkeypatch, tmp_path):
    # The final full-suite rerun fails with no parseable test entries (config error, global
    # setup failure, or timeout): no repair can be confirmed, so healed must be 0 (#212).
    a = tmp_path / "a.spec.ts"
    a.write_text("x")

    def _heal(path, log, context, dry_run, memory_enabled):
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    # Focused rerun heals; the final rerun fails with an empty (unparseable) log.
    monkeypatch.setattr(
        cli, "run_playwright", _suite_runner((a,), lambda t: (False, "focused"), False, ())
    )
    summary = cli._heal_suite("", [], dry_run=False)
    assert summary.total_failed == 1
    assert summary.healed == 0
    assert summary.is_success is False
    assert all(not r.is_success for r in summary.results)


def test_suite_dry_run_reports_non_successful_preview(monkeypatch, tmp_path):
    # --dry-run commits nothing, so the final full-suite rerun cannot verify a heal: the
    # aggregate must be a non-successful preview even when focused repairs succeed (#212).
    a = tmp_path / "a.spec.ts"
    a.write_text("x")

    def _heal(path, log, context, dry_run, memory_enabled):
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    monkeypatch.setattr(
        cli, "run_playwright", _suite_runner((a,), lambda t: (False, "focused"), True)
    )
    summary = cli._heal_suite("", [], dry_run=True)
    assert summary.total_failed == 1
    assert summary.healed == 1  # the focused preview still shows what would heal
    assert summary.is_success is False  # but no final verification -> not success


def test_suite_final_rerun_reveals_failures_in_scanner_order(monkeypatch, tmp_path):
    # Newly-revealed final-rerun failures must be appended in first-seen order (the scanner's
    # dedup contract), not hash-randomized set order (#212).
    a = tmp_path / "a.spec.ts"
    a.write_text("x")
    b = tmp_path / "b.spec.ts"
    b.write_text("y")
    c = tmp_path / "c.spec.ts"
    c.write_text("z")

    def _heal(path, log, context, dry_run, memory_enabled):
        return RepairSummary(test_script_path=str(path), is_success=True, loop_count=1)

    monkeypatch.setattr(cli, "_heal_file", _heal)
    # Initial fails only on `a`; the final rerun reveals `b` then `c` (both new).
    monkeypatch.setattr(
        cli, "run_playwright", _suite_runner((a,), lambda t: (False, "focused"), False, (b, c))
    )
    summary = cli._heal_suite("", [], dry_run=False)
    revealed = [r.test_script_path for r in summary.results if not r.is_success]
    assert revealed == [str(b), str(c)]
