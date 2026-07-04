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
from app.runner import run_playwright
from app.schemas import PatchInstruction, RepairSummary
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


@app.command()
def heal(
    test_path: Path = typer.Argument(..., exists=True, help="failing Playwright test file"),
    log_file: Optional[Path] = typer.Option(
        None, "--log", help="raw Playwright failure log; if omitted, the test is run to capture it"
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
    json_output: bool = typer.Option(False, "--json", help="emit RepairSummary JSON to stdout"),
) -> None:
    """Repair a single failing test. Exit 0 if fixed, non-zero otherwise."""
    configure_logging(settings.log_level)
    original_code = test_path.read_text()

    # 1. Acquire the failure log: reuse --log, else run the test once to capture it.
    if log_file is not None:
        raw_log = log_file.read_text()
    else:
        passed, raw_log = run_playwright(str(test_path))
        if passed:
            console.print("[green]test already passes[/green] — nothing to heal")
            raise typer.Exit(code=0)

    initial_state: AgentState = {
        "test_script_path": str(test_path),
        "original_code": original_code,
        "current_code": original_code,
        "error_log": parse_error_log(raw_log),
        "dom_diff_context": [
            d.model_dump() for d in analyze_diff(_read_diff(diff_file, diff_base))
        ],
        "analysis_report": "",
        "patch_instructions": {},
        "loop_count": 0,
        "is_success": False,
    }

    logger.info("repair_run_started", test_script_path=str(test_path))
    final_state = build_graph().invoke(initial_state)

    # 2. Persist policy: restore the original on failure or in dry-run mode.
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
    if json_output:
        typer.echo(summary.model_dump_json())
    status = "fixed" if summary.is_success else "not fixed"
    console.print(f"[bold]{status}[/bold] after {summary.loop_count} loop(s)")

    logger.info("repair_run_finished", is_success=summary.is_success, loop_count=summary.loop_count)
    raise typer.Exit(code=0 if summary.is_success else 1)


if __name__ == "__main__":
    app()
