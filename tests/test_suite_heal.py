"""Suite-mode orchestration, with Playwright and per-file healing mocked out."""

import app.cli as cli
from app.schemas import RepairSummary


def _combined(*paths) -> str:
    return "".join(f"  {i + 1}) {p}:1:1 › t\n" for i, p in enumerate(paths))


def test_suite_passes_nothing_to_heal(monkeypatch):
    monkeypatch.setattr(cli, "run_playwright", lambda target="": (True, ""))
    summary = cli._heal_suite("", [], dry_run=False)
    assert summary.total_failed == 0
    assert summary.is_success is True


def test_suite_all_healed(monkeypatch, tmp_path):
    a, b = tmp_path / "a.spec.ts", tmp_path / "b.spec.ts"
    a.write_text("x")
    b.write_text("y")
    combined = _combined(a, b)

    def fake_run(target=""):
        return (False, combined) if target == "" else (False, "focused")

    monkeypatch.setattr(cli, "run_playwright", fake_run)
    monkeypatch.setattr(
        cli,
        "_heal_file",
        lambda p, log, ctx, dry, no_mem=False: RepairSummary(
            test_script_path=str(p), is_success=True, loop_count=1
        ),
    )
    summary = cli._heal_suite("", [], dry_run=False)
    assert (summary.total_failed, summary.healed, summary.is_success) == (2, 2, True)


def test_suite_partial_heal_is_not_success(monkeypatch, tmp_path):
    a, b = tmp_path / "a.spec.ts", tmp_path / "b.spec.ts"
    a.write_text("x")
    b.write_text("y")
    combined = _combined(a, b)
    monkeypatch.setattr(
        cli, "run_playwright", lambda target="": (False, combined) if target == "" else (False, "f")
    )
    monkeypatch.setattr(
        cli,
        "_heal_file",
        lambda p, log, ctx, dry, no_mem=False: RepairSummary(
            test_script_path=str(p), is_success=(p.name == "a.spec.ts"), loop_count=1
        ),
    )
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
