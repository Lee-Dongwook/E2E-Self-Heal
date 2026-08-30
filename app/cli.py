"""CLI core: the single entry point for a repair run (also what CI invokes)."""

import difflib
import json
import subprocess
from pathlib import Path

import structlog
import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from typer.core import TyperGroup

from app.config import settings
from app.graph import build_graph, build_review_graph
from app.healing_history import append_record, make_record
from app.logging import configure_logging
from app.notifications import notify_heal_outcome
from app.preprocess.aria_snapshot import read_failure_snapshot
from app.preprocess.diff_ast_analyzer import analyze_diff
from app.preprocess.error_log_parser import parse_error_log
from app.preprocess.failure_scanner import scan_failing_tests
from app.runner import run_playwright
from app.sandbox import (
    SandboxViolation,
    assert_auto_discovered_target,
    assert_command_allowed,
    assert_read_allowed,
    assert_write_allowed,
)
from app.schemas import (
    PatchInstruction,
    RepairSummary,
    ReviewFinding,
    ReviewReport,
    SelectorHint,
    SuiteSummary,
)
from app.shadow.runtime import run_shadow
from app.shadow.schemas import ShadowRunResult
from app.state import AgentState
from app.utils.files import atomic_write

WORKFLOW_TARGET_PATH = Path(".github/workflows/e2e-healer.yml")


class _DefaultCommandGroup(TyperGroup):
    """Route a bare invocation to `heal` so `e2e-healer <path>` keeps working."""

    default_command = "heal"

    def parse_args(self, ctx, args):
        if not args or (args[0] not in self.commands and not args[0].startswith("-")):
            args = [self.default_command, *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    help="AI-driven E2E test self-healing engine", cls=_DefaultCommandGroup, no_args_is_help=False
)
console = Console(stderr=True)
logger = structlog.get_logger(__name__)


@app.callback(invoke_without_command=True)
def main(
    shadow: bool = typer.Option(
        False, "--shadow", help="run the Shadow Testing runtime (under development)"
    ),
) -> None:
    """AI-driven E2E test self-healing engine."""
    if not shadow:
        return
    configure_logging(settings.log_level)
    logger.info("shadow_mode_invoked")

    result = run_shadow()
    if isinstance(result, ShadowRunResult):
        status = "passed" if result.is_success else "failed"
        renderable = (
            f"[bold]{status}[/bold] | matched={result.matched_count} "
            f"missed={result.missed_count} score={result.score:.2f}"
        )
    else:
        renderable = result
    console.print(Panel(renderable, title="Shadow Testing", border_style="yellow"))
    raise typer.Exit(code=0)


def _read_diff(diff_file: Path | None, diff_base: str | None) -> str:
    if diff_file is not None:
        assert_read_allowed(diff_file)
        return diff_file.read_text()
    cmd = ["git", "diff", f"{diff_base}...HEAD"] if diff_base else ["git", "diff"]
    assert_command_allowed(cmd, reason="git_diff")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        logger.error("git_diff_failed", cmd=cmd, reason="git_not_found", error=str(exc))
        console.print(
            Panel(
                "git executable not found — is git installed and on PATH?",
                title="Cannot read git diff",
                border_style="red",
            )
        )
        raise typer.Exit(code=2) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or "git exited with a non-zero status."
        logger.error(
            "git_diff_failed",
            cmd=cmd,
            returncode=exc.returncode,
            error=str(exc),
            stderr=detail,
        )
        console.print(
            Panel(
                f"{detail}\n\n"
                "Check that --diff-base is a valid ref and that this is a git repository.",
                title="Cannot read git diff",
                border_style="red",
            )
        )
        raise typer.Exit(code=2) from exc
    return result.stdout


def _render_diff(original: str, patched: str, path: str) -> None:
    text = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    console.print(Syntax(text, "diff", theme="ansi_dark") if text else "[dim]no changes[/dim]")


def _restore_original_file(path: Path, original_code: str) -> None:
    """Restore ``path`` to ``original_code`` if a candidate was left on disk.

    Best-effort: failures are logged, never masking the triggering exception.
    """
    try:
        if path.read_text() != original_code:
            atomic_write(path, original_code)
    except Exception as exc:
        logger.error("restore_original_failed", test_script_path=str(path), error=str(exc))


def _heal_file(
    test_path: Path,
    raw_log: str,
    dom_diff_context: list[dict],
    dry_run: bool,
    memory_enabled: bool = True,
) -> RepairSummary:
    assert_read_allowed(test_path)
    assert_write_allowed(test_path, reason="repair_target")
    original_code = test_path.read_text()
    parsed_error_log = parse_error_log(raw_log)
    initial_state: AgentState = {
        "test_script_path": str(test_path),
        "original_code": original_code,
        "current_code": original_code,
        "rollback_code": original_code,
        "error_log": parsed_error_log,
        "dom_diff_context": dom_diff_context,
        "dom_snapshot": read_failure_snapshot(Path(settings.test_results_dir), test_path),
        "analysis_report": "",
        "memory_enabled": memory_enabled,
        "patch_instructions": {},
        "verification_report": {},
        "review_report": {},
        "loop_count": 0,
        "is_success": False,
    }
    logger.info("repair_run_started", test_script_path=str(test_path))
    # Restore the original for any non-committing outcome (failed loop, --dry-run,
    # post-write exception); commit only on a successful non-dry-run.
    committed = False
    try:
        final_state = build_graph().invoke(initial_state)
        _render_diff(original_code, final_state["current_code"], str(test_path))
        instructions = final_state["patch_instructions"] or {}
        summary = RepairSummary(
            test_script_path=final_state["test_script_path"],
            is_success=final_state["is_success"],
            loop_count=final_state["loop_count"],
            instructions=[PatchInstruction(**i) for i in instructions.get("instructions", [])],
        )
        committed = not dry_run and final_state["is_success"]
        if (
            memory_enabled
            and committed
            and final_state.get("memory_report", {}).get("source") != "memory"
        ):
            record = make_record(
                error_log=parsed_error_log,
                instructions=summary.instructions,
                test_script_path=summary.test_script_path,
                source="llm",
            )
            if record is not None:
                try:
                    if append_record(record):
                        logger.info("healing_recorded", test_script_path=summary.test_script_path)
                except Exception as exc:
                    logger.warning(
                        "healing_record_not_saved",
                        test_script_path=summary.test_script_path,
                        error=str(exc),
                    )
        logger.info(
            "repair_run_finished", is_success=summary.is_success, loop_count=summary.loop_count
        )
        return summary
    finally:
        if not committed:
            _restore_original_file(test_path, original_code)


def _heal_suite(
    suite_target: str,
    dom_diff_context: list[dict],
    dry_run: bool,
    memory_enabled: bool = True,
) -> SuiteSummary:
    passed, raw_log = run_playwright(suite_target)
    if passed:
        return SuiteSummary(total_failed=0, healed=0, is_success=True)
    results: list[RepairSummary] = []
    for rel in scan_failing_tests(raw_log):
        path = Path(rel)
        # Targets parsed from reporter output are untrusted: require them to resolve inside
        # the workspace (all modes except off) rather than authorizing an external path by
        # its basename (Issue #211).
        try:
            resolved = assert_auto_discovered_target(path)
        except SandboxViolation as exc:
            logger.warning("failing_test_sandbox_denied", path=rel, error=str(exc))
            # Keep the denied failure visible as an unresolved suite result rather than
            # silently dropping it, so the suite is not reported as fully healed.
            results.append(RepairSummary(test_script_path=rel, is_success=False, loop_count=0))
            continue
        # Use the validated canonical path for all filesystem access; keep the
        # workspace-relative value only for logging/display.
        if not resolved.exists():
            logger.warning("failing_test_not_found", path=rel)
            continue
        rerun_passed, focused_log = run_playwright(str(resolved))
        if rerun_passed:
            results.append(RepairSummary(test_script_path=rel, is_success=True, loop_count=0))
            continue
        results.append(_heal_file(resolved, focused_log, dom_diff_context, dry_run, memory_enabled))
    healed = sum(1 for r in results if r.is_success)
    return SuiteSummary(
        total_failed=len(results),
        healed=healed,
        is_success=len(results) > 0 and healed == len(results),
        results=results,
    )


def _review_file(test_path: Path, raw_log: str, dom_diff_context: list[dict]) -> ReviewReport:
    assert_read_allowed(test_path)
    current_code = test_path.read_text()
    initial_state: AgentState = {
        "test_script_path": str(test_path),
        "original_code": current_code,
        "current_code": current_code,
        "error_log": parse_error_log(raw_log),
        "dom_diff_context": dom_diff_context,
        "dom_snapshot": read_failure_snapshot(Path(settings.test_results_dir), test_path),
        "analysis_report": "",
        "patch_instructions": {},
        "verification_report": {},
        "review_report": {},
        "loop_count": 0,
        "is_success": False,
    }
    logger.info("review_run_started", test_script_path=str(test_path))
    final_state = build_review_graph().invoke(initial_state)
    review_result = final_state["review_report"]
    findings = [ReviewFinding(**f) for f in review_result.get("findings", [])]
    report = ReviewReport(
        test_script_path=str(test_path),
        findings=findings,
        has_findings=len(findings) > 0,
        is_complete=review_result.get("is_complete", True),
        error=review_result.get("error"),
    )
    logger.info("review_run_finished", finding_count=len(findings), is_complete=report.is_complete)
    return report


def _render_findings(report: ReviewReport) -> None:
    if not report.findings:
        console.print("[dim]no findings[/dim]")
        return
    table = Table(title="Source-level review findings", show_lines=True)
    table.add_column("File:Line", style="cyan", no_wrap=True)
    table.add_column("Root cause")
    table.add_column("Suggestion")
    table.add_column("Recommended selector", style="green")
    for finding in report.findings:
        table.add_row(
            f"{finding.file}:{finding.line}",
            finding.root_cause,
            finding.suggestion,
            finding.recommended_selector,
        )
    console.print(table)


@app.command()
def benchmark() -> None:
    """Compare full-file and semantic-context prompt tokens for shipped examples."""
    configure_logging(settings.log_level)
    from app.benchmark import run_example_benchmark

    results = run_example_benchmark()
    table = Table(title="Diagnoser prompt token benchmark (cl100k_base estimate)")
    table.add_column("Example", style="cyan")
    table.add_column("Context strategy")
    table.add_column("Full-file", justify="right")
    table.add_column("Chunked", justify="right")
    table.add_column("Saved", justify="right", style="green")
    for result in results:
        table.add_row(
            result.name,
            result.context_strategy,
            str(result.full_prompt_tokens),
            str(result.chunked_prompt_tokens),
            f"{result.tokens_saved} ({result.savings_percent:.1f}%)",
        )
    console.print(table)


@app.command()
def heal(
    test_path: Path | None = typer.Argument(
        None, help="failing test file; a directory or omitting it heals the whole suite"
    ),
    log_file: Path | None = typer.Option(
        None, "--log", help="raw Playwright failure log (single-file mode); else the test is run"
    ),
    diff_file: Path | None = typer.Option(
        None, "--diff", help="git diff file; defaults to `git diff`"
    ),
    diff_base: str | None = typer.Option(
        None, "--diff-base", help="git ref to diff against as base...HEAD (e.g. a PR base)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="run the loop but restore the original file; write nothing"
    ),
    memory_enabled: bool = typer.Option(
        True,
        "--memory/--no-memory",
        help="use local healing history for first-pass repair and verified repair storage",
    ),
    app_url: str | None = typer.Option(
        None, "--app-url", help="URL the Selector Verifier loads to check patched selectors"
    ),
    json_output: bool = typer.Option(False, "--json", help="emit JSON summary to stdout"),
    selector_hint: str | None = typer.Option(
        None,
        "--selector-hint",
        help='JSON selector hint for pinpoint healing (e.g. \'{"type":"role","value":"button[name=Submit]","original":"#old-btn"}\')',
    ),
) -> None:
    """Repair a failing test (or the whole suite). Exit 0 if everything is fixed, else non-zero.

    Target authorization differs by how the target was obtained. An explicit ``test_path`` a
    developer typed is trusted input and is authorized only by the sandbox read/write globs
    (so, in relaxed mode, an explicit path may live outside ``workspace_root``). Targets the
    suite healer auto-discovers from Playwright reporter output are untrusted and are held to
    the stricter ``assert_auto_discovered_target`` rule — they must resolve inside
    ``workspace_root`` in every mode except ``off`` (Issue #211).
    """
    configure_logging(settings.log_level)
    try:
        if app_url is not None:
            settings.app_url = app_url
        if test_path is not None:
            assert_read_allowed(test_path)
            if not test_path.exists():
                console.print(f"[red]path not found:[/red] {test_path}")
                raise typer.Exit(code=2)

        # Parse and validate selector hint FIRST (Issue #119)
        parsed_hint = None
        if selector_hint is not None:
            try:
                parsed_hint = SelectorHint.model_validate_json(selector_hint)
                logger.info(
                    "selector_hint_parsed",
                    type=parsed_hint.type,
                    value=parsed_hint.value,
                    original=parsed_hint.original,
                )
            except Exception as e:
                console.print(f"[red]Invalid --selector-hint JSON:[/red] {e}")
                raise typer.Exit(code=2)

        dom_diff_context = [d.model_dump() for d in analyze_diff(_read_diff(diff_file, diff_base))]

        # Now inject the already-validated hint into context
        if parsed_hint is not None:
            dom_diff_context.append(
                {
                    "type": "selector_hint",
                    "hint_type": parsed_hint.type,
                    "value": parsed_hint.value,
                    "original": parsed_hint.original,
                    "confidence": parsed_hint.confidence,
                    "priority": "high",
                }
            )
            logger.info("selector_hint_injected", value=parsed_hint.value)

        if test_path is not None and test_path.is_file():
            assert_write_allowed(test_path, reason="repair_target")
            if log_file is not None:
                assert_read_allowed(log_file)
                raw_log = log_file.read_text()
            else:
                passed, raw_log = run_playwright(str(test_path))
                if passed:
                    console.print("[green]test already passes[/green] — nothing to heal")
                    raise typer.Exit(code=0)
            summary = _heal_file(test_path, raw_log, dom_diff_context, dry_run, memory_enabled)
            if json_output:
                typer.echo(summary.model_dump_json())

            # Notify Slack (Issue #124)
            notify_heal_outcome(summary)

            status = "fixed" if summary.is_success else "not fixed"
            console.print(f"[bold]{status}[/bold] after {summary.loop_count} loop(s)")
            raise typer.Exit(code=0 if summary.is_success else 1)

        suite = _heal_suite(
            str(test_path) if test_path is not None else "",
            dom_diff_context,
            dry_run,
            memory_enabled,
        )
        if suite.total_failed == 0 and suite.is_success:
            console.print("[green]suite passes[/green] — nothing to heal")
            raise typer.Exit(code=0)
        if suite.total_failed == 0:
            console.print("[yellow]suite failed but no test files could be parsed/found[/yellow]")
            raise typer.Exit(code=1)
        if json_output:
            typer.echo(suite.model_dump_json())

        # Notify Slack for each result in suite (Issue #124)
        for res in suite.results:
            notify_heal_outcome(res)

        console.print(f"[bold]{suite.healed}/{suite.total_failed}[/bold] test(s) healed")
        raise typer.Exit(code=0 if suite.is_success else 1)
    except SandboxViolation as exc:
        console.print(f"[red]sandbox denied:[/red] {exc}")
        raise typer.Exit(code=2) from exc


@app.command()
def review(
    test_path: Path = typer.Argument(..., help="failing test file to review (never modified)"),
    log_file: Path | None = typer.Option(
        None, "--log", help="raw Playwright failure log; else the test is run to produce one"
    ),
    diff_file: Path | None = typer.Option(
        None, "--diff", help="git diff file; defaults to `git diff`"
    ),
    diff_base: str | None = typer.Option(
        None, "--diff-base", help="git ref to diff against as base...HEAD (e.g. a PR base)"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="emit the ReviewReport JSON to stdout for the CI wrapper"
    ),
) -> None:
    """Review a failing test and suggest source-level fixes as PR comments — never edits it."""
    configure_logging(settings.log_level)
    try:
        assert_read_allowed(test_path)
        if not test_path.exists():
            console.print(f"[red]path not found:[/red] {test_path}")
            raise typer.Exit(code=2)

        dom_diff_context = [d.model_dump() for d in analyze_diff(_read_diff(diff_file, diff_base))]

        if log_file is not None:
            assert_read_allowed(log_file)
            raw_log = log_file.read_text()
        else:
            passed, raw_log = run_playwright(str(test_path))
            if passed:
                console.print("[green]test passes[/green] — nothing to review")
                raise typer.Exit(code=0)
        report = _review_file(test_path, raw_log, dom_diff_context)
        if json_output:
            typer.echo(report.model_dump_json())
        if not report.is_complete:
            console.print(f"[red]review incomplete:[/red] {report.error}")
            raise typer.Exit(code=1)
        _render_findings(report)
        console.print(f"[bold]{len(report.findings)}[/bold] source-level suggestion(s)")
        raise typer.Exit(code=0)
    except SandboxViolation as exc:
        console.print(f"[red]sandbox denied:[/red] {exc}")
        raise typer.Exit(code=2) from exc


@app.command()
def init(
    scaffold: bool = typer.Option(
        False, "--scaffold", "-s", help="Also scaffold a starter GitHub Actions workflow."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing workflow configuration if it exists."
    ),
) -> None:
    """Analyze the repository and print a readiness report for E2E self-healing."""
    console.print(Panel("E2E Self-Heal Readiness Report", style="bold blue"))

    pw_configs = list(Path(".").glob("playwright.config.*"))
    has_pw_config = len(pw_configs) > 0
    pw_config_name = pw_configs[0].name if has_pw_config else "None"

    test_patterns = ["**/*.spec.ts", "**/*.test.ts", "**/*.spec.js", "**/*.test.js"]
    test_files = []
    for pattern in test_patterns:
        for path in Path(".").rglob(pattern.split("/")[-1]):
            if "node_modules" not in path.parts and ".git" not in path.parts:
                if path.match(pattern):
                    test_files.append(path)
    test_files = list(set(test_files))
    test_count = len(test_files)

    test_dirs = list(set(f.parent for f in test_files))
    test_dir_str = ", ".join(str(d) for d in test_dirs) if test_dirs else "Not found"

    llm_provider = settings.llm_provider
    has_api_key = bool(settings.llm_api_key)

    is_provider_ready = (llm_provider == "ollama") or has_api_key

    pw_installed = False
    pkg_json = Path("package.json")
    if pkg_json.exists():
        try:
            pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
            deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
            pw_installed = "playwright" in deps or "@playwright/test" in deps
        except Exception:
            pass

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Check", style="cyan", justify="right")
    table.add_column("Status", style="white")

    table.add_row(
        "Playwright Config",
        f"[green]✓ Found[/green] ({pw_config_name})" if has_pw_config else "[red]✗ Not found[/red]",
    )
    table.add_row(
        "Playwright in package.json",
        "[green]✓ Yes[/green]" if pw_installed else "[yellow]⚠ Not detected[/yellow]",
    )
    table.add_row(
        "Test Files",
        f"[green]✓ {test_count} found[/green]" if test_count > 0 else "[red]✗ 0 found[/red]",
    )
    if test_dirs and test_count > 0:
        table.add_row("Test Directories", f"[dim]{test_dir_str}[/dim]")

    table.add_row("LLM Provider", f"[green]✓ {llm_provider}[/green]")
    if llm_provider == "ollama":
        api_key_status = (
            "[green]✓ Not required (local model)[/green]"
            if not has_api_key
            else "[green]✓ Configured[/green]"
        )
    else:
        api_key_status = (
            "[green]✓ Configured[/green]"
            if has_api_key
            else "[red]✗ Missing (set E2E_HEALER_LLM_API_KEY in .env)[/red]"
        )
    table.add_row("API Key", api_key_status)

    console.print(table)
    console.print()

    is_playwright_present = has_pw_config or pw_installed or test_count > 0
    is_ready = is_provider_ready and is_playwright_present

    if not is_playwright_present:
        console.print(
            Panel(
                "[yellow]Warning:[/yellow] This does not look like a Playwright project.\n"
                "Please run this command in the root directory of a Playwright project.",
                title="Playwright Not Detected",
                border_style="yellow",
            )
        )

    if not is_provider_ready:
        console.print(
            Panel(
                "[yellow]Action Required:[/yellow] Please set your LLM API key "
                "in your `.env` file or environment.\n"
                "Example: `E2E_HEALER_LLM_API_KEY=your_key_here`\n"
                "See: https://github.com/Lee-Dongwook/E2E-Self-Heal#configuration",
                title="Configuration Needed",
                border_style="yellow",
            )
        )

    if is_ready:
        console.print(
            Panel(
                "[green]✓ Repository is ready for E2E Self-Healing![/green]\n\n"
                "Next steps:\n"
                "  1. Ensure browsers are installed: [bold]npx playwright install[/bold]\n"
                "  2. Run a test heal: [bold]e2e-healer <path-to-failing-test>[/bold]\n"
                "  3. Use [bold]--scaffold[/bold] flag to generate a GitHub Actions workflow.",
                title="Ready to Go",
                border_style="green",
            )
        )
        exit_code = 0
    else:
        console.print(
            Panel(
                "Once you have resolved the configuration issues and have Playwright tests, "
                "you'll be ready to use E2E Self-Healing!",
                title="Next Steps",
                border_style="yellow",
            )
        )
        exit_code = 1

    if scaffold:
        if WORKFLOW_TARGET_PATH.exists() and not force:
            console.print(
                f"[yellow]Workflow file already exists at {WORKFLOW_TARGET_PATH}. Use --force to overwrite.[/yellow]"
            )
            raise typer.Exit(code=1)
        else:
            try:
                WORKFLOW_TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
                yaml_template = """name: E2E Self-Healing CI
on:
  push:
    branches: [ main ]
jobs:
  heal:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # add steps to invoke e2e-healer
"""
                WORKFLOW_TARGET_PATH.write_text(yaml_template)
                console.print(
                    f"[green]Successfully scaffolded starter workflow at {WORKFLOW_TARGET_PATH}![/green]"
                )
            except Exception as e:
                console.print(f"[red]Failed to write workflow file: {e}[/red]")
                raise typer.Exit(code=1)

    raise typer.Exit(code=exit_code)


if __name__ == "__main__":
    app()
