"""CLI core: the single entry point for a repair run (also what CI invokes)."""

import difflib
import subprocess
from pathlib import Path
from typing import Optional

import structlog
import typer
from rich.console import Console
from rich.syntax import Syntax

from app.config import settings
from app.graph import build_graph
from app.logging import configure_logging
from app.preprocess.diff_ast_analyzer import analyze_diff
from app.preprocess.error_log_parser import parse_error_log
from app.preprocess.failure_scanner import scan_failing_tests
from app.runner import run_playwright
from app.schemas import PatchInstruction, RepairSummary, SuiteSummary
from app.state import AgentState
from app.utils.files import atomic_write

app = typer.Typer(help="AI-driven E2E test self-healing engine")
console = Console(stderr=True)  # human output on stderr; JSON summary on stdout
logger = structlog.get_logger(__name__)


def _read_diff(diff_file: Optional[Path], diff_base: Optional[str]) -> str:
    """Return the git diff from a file, else `git diff [base...HEAD]`.

    ``diff_base`` (e.g. a PR base ref) scopes the diff to `base...HEAD`, which is what
    the CI/PR path needs; without it we fall back to the working-tree `git diff`.
    """
    if diff_file is not None:
        return diff_file.read_text()
    cmd = ["git", "diff", f"{diff_base}...HEAD"] if diff_base else ["git", "diff"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def _render_diff(original: str, patched: str, path: str) -> None:
    """Print a unified diff of the applied changes to the console (stderr)."""
    text = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    console.print(Syntax(text, "diff", theme="ansi_dark") if text else "[dim]no changes[/dim]")


def _heal_file(
    test_path: Path, raw_log: str, dom_diff_context: list[dict], dry_run: bool
) -> RepairSummary:
    """Run the repair graph on one test file and return its machine-readable summary.

    Restores the original on failure or in dry-run mode; the diff is rendered to stderr.
    """
    original_code = test_path.read_text()
    initial_state: AgentState = {
        "test_script_path": str(test_path),
        "original_code": original_code,
        "current_code": original_code,
        "error_log": parse_error_log(raw_log),
        "dom_diff_context": dom_diff_context,
        "analysis_report": "",
        "patch_instructions": {},
        "verification_report": {},
        "loop_count": 0,
        "is_success": False,
    }

    logger.info("repair_run_started", test_script_path=str(test_path))
    final_state = build_graph().invoke(initial_state)

    if dry_run or not final_state["is_success"]:
        atomic_write(test_path, original_code)

    _render_diff(original_code, final_state["current_code"], str(test_path))

    instructions = final_state["patch_instructions"] or {}
    summary = RepairSummary(
        test_script_path=final_state["test_script_path"],
        is_success=final_state["is_success"],
        loop_count=final_state["loop_count"],
        instructions=[PatchInstruction(**i) for i in instructions.get("instructions", [])],
    )
    logger.info("repair_run_finished", is_success=summary.is_success, loop_count=summary.loop_count)
    return summary


def _heal_suite(suite_target: str, dom_diff_context: list[dict], dry_run: bool) -> SuiteSummary:
    """Run the whole suite (or a directory), then heal each failing test file.

    ``suite_target`` empty means the whole suite. A per-file re-run gives each file a
    focused failure log before healing; files that pass on re-run are recorded as fixed.
    """
    passed, raw_log = run_playwright(suite_target)
    if passed:
        return SuiteSummary(total_failed=0, healed=0, is_success=True)

    results: list[RepairSummary] = []
    for rel in scan_failing_tests(raw_log):
        path = Path(rel)
        if not path.exists():
            logger.warning("failing_test_not_found", path=rel)
            continue
        rerun_passed, focused_log = run_playwright(rel)
        if rerun_passed:  # flaky or already fixed by an earlier file's patch
            results.append(RepairSummary(test_script_path=rel, is_success=True, loop_count=0))
            continue
        results.append(_heal_file(path, focused_log, dom_diff_context, dry_run))

    healed = sum(1 for r in results if r.is_success)
    return SuiteSummary(
        total_failed=len(results),
        healed=healed,
        is_success=len(results) > 0 and healed == len(results),
        results=results,
    )


@app.command()
def heal(
    test_path: Optional[Path] = typer.Argument(
        None, help="failing test file; a directory or omitting it heals the whole suite"
    ),
    log_file: Optional[Path] = typer.Option(
        None, "--log", help="raw Playwright failure log (single-file mode); else the test is run"
    ),
    diff_file: Optional[Path] = typer.Option(
        None, "--diff", help="git diff file; defaults to `git diff`"
    ),
    diff_base: Optional[str] = typer.Option(
        None, "--diff-base", help="git ref to diff against as base...HEAD (e.g. a PR base)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="run the loop but restore the original file; write nothing"
    ),
    app_url: Optional[str] = typer.Option(
        None, "--app-url", help="URL the Selector Verifier loads to check patched selectors"
    ),
    json_output: bool = typer.Option(False, "--json", help="emit JSON summary to stdout"),
) -> None:
    """Repair a failing test (or the whole suite). Exit 0 if everything is fixed, else non-zero."""
    configure_logging(settings.log_level)
    if app_url is not None:
        settings.app_url = app_url  # CLI flag overrides the E2E_HEALER_APP_URL setting
    if test_path is not None and not test_path.exists():
        console.print(f"[red]path not found:[/red] {test_path}")
        raise typer.Exit(code=2)

    dom_diff_context = [d.model_dump() for d in analyze_diff(_read_diff(diff_file, diff_base))]

    # Single-file mode: an existing file path.
    if test_path is not None and test_path.is_file():
        if log_file is not None:
            raw_log = log_file.read_text()
        else:
            passed, raw_log = run_playwright(str(test_path))
            if passed:
                console.print("[green]test already passes[/green] — nothing to heal")
                raise typer.Exit(code=0)
        summary = _heal_file(test_path, raw_log, dom_diff_context, dry_run)
        if json_output:
            typer.echo(summary.model_dump_json())
        status = "fixed" if summary.is_success else "not fixed"
        console.print(f"[bold]{status}[/bold] after {summary.loop_count} loop(s)")
        raise typer.Exit(code=0 if summary.is_success else 1)

    # Suite mode: no path or a directory.
    suite = _heal_suite(str(test_path) if test_path is not None else "", dom_diff_context, dry_run)
    if suite.total_failed == 0 and suite.is_success:
        console.print("[green]suite passes[/green] — nothing to heal")
        raise typer.Exit(code=0)
    if suite.total_failed == 0:
        console.print("[yellow]suite failed but no test files could be parsed/found[/yellow]")
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(suite.model_dump_json())
    console.print(f"[bold]{suite.healed}/{suite.total_failed}[/bold] test(s) healed")
    raise typer.Exit(code=0 if suite.is_success else 1)


if __name__ == "__main__":
    app()
