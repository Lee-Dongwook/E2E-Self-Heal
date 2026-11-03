"""Keep release pins and documented defaults in step with the code (#457).

Two values are written by hand across the README (every language), the docs site, the demo page,
``.env.example`` and both lockfiles, and went stale before (#421): the release version (the
GitHub Action examples' ``Lee-Dongwook/E2E-Self-Heal@vX.Y.Z`` pins and the core's locked version
in ``uv.lock`` and ``product/api/uv.lock``), and the default NVIDIA model.

    uv run python scripts/sync_release_pins.py          # rewrite every mismatch
    uv run python scripts/sync_release_pins.py --check  # change nothing; exit 1 on a mismatch

The version comes from ``pyproject.toml`` and the model from ``app.config.Settings``, so a
release only has to change those. Action pins are found in every tracked file except the
changelog, which records history. The default model is replaced only in the specific sentences
and table rows that state it, never by searching the whole tree: other text, such as the
measured free-tier results in the configuration reference, names models for other reasons.
Each of those spots must match exactly where expected, so rewording one fails ``--check``
instead of silently dropping it. ``e2e-healer init --scaffold`` needs nothing here: it pins the
version of the CLI that runs it.
"""

import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]
console = Console(stderr=True)

# History is never rewritten.
_NEVER_TOUCHED = {"CHANGELOG.md"}
_VERSION = r"\d+\.\d+\.\d+"


@dataclass(frozen=True)
class Rule:
    """A value to keep in sync: ``pattern`` captures (prefix, value, suffix).

    ``files`` lists the files that must contain it; ``None`` means every tracked text file, where
    any number of matches, including none, is fine.
    """

    name: str
    key: str  # "version" or "model"
    pattern: re.Pattern[str]
    files: tuple[str, ...] | None = None


def _rule(name: str, key: str, pattern: str, files: tuple[str, ...] | None = None) -> Rule:
    return Rule(name, key, re.compile(pattern, re.MULTILINE), files)


_READMES = ("README.md", "README.ko.md", "README.ja.md", "README.zh-CN.md")

RULES: tuple[Rule, ...] = (
    _rule("action pin", "version", rf"(Lee-Dongwook/E2E-Self-Heal@v)({_VERSION})()"),
    _rule(
        "action pin example",
        "version",
        rf"(\(e\.g\. `@v)({_VERSION})(`\))",
        ("docs-site/docs/reference/github-action.mdx",),
    ),
    # The product API locks the core as an editable path dependency, so its lockfile records
    # the core's version too; a stale one fails the `api` job's `uv sync --locked`.
    _rule(
        "locked core version",
        "version",
        rf'(^name = "ai-driven-e2e"\nversion = ")({_VERSION})(")',
        ("uv.lock", "product/api/uv.lock"),
    ),
    _rule(
        "env table row",
        "model",
        r"(\| `E2E_HEALER_NVIDIA_MODEL`\s*\| `)([^`]+)(`)",
        (*_READMES, "docs-site/docs/reference/configuration.mdx"),
    ),
    _rule("env example", "model", r"(^E2E_HEALER_NVIDIA_MODEL=)(\S+)()", (".env.example",)),
    _rule(
        "provider matrix",
        "model",
        r"(^\| `nvidia` .*?\bdefault (?:model )?`)([^`]+)(`)",
        ("README.md", "docs-site/docs/reference/configuration.mdx"),
    ),
    _rule("intro (en)", "model", r"(\(default model `)([^`]+)(`\))", ("README.md",)),
    _rule("intro (ko)", "model", r"(\(기본 모델 `)([^`]+)(`\))", ("README.ko.md",)),
    _rule("intro (ja)", "model", r"(（デフォルトモデルは `)([^`]+)(`）)", ("README.ja.md",)),
    _rule("intro (zh-CN)", "model", r"(（默认使用 `)([^`]+)(` 模型）)", ("README.zh-CN.md",)),
    _rule(
        "quickstart",
        "model",
        r"(\(free key,\s*`)([^`]+)(`\))",
        ("docs-site/docs/getting-started/quickstart-cli.mdx",),
    ),
)


@dataclass
class Report:
    """What a sync found: values that differed, and expected spots that are missing."""

    changes: list[tuple[str, str, str, str]] = field(default_factory=list)  # file, rule, old, new
    missing: list[tuple[str, str]] = field(default_factory=list)  # file, rule

    @property
    def clean(self) -> bool:
        return not self.changes and not self.missing


def current_values(root: Path = ROOT) -> dict[str, str]:
    """The release version and the default NVIDIA model, read from the code."""
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    default_model = Settings.model_fields["nvidia_model"].default
    return {"version": project["project"]["version"], "model": str(default_model)}


def tracked_files(root: Path = ROOT) -> list[str]:
    """Tracked paths relative to ``root``, from git."""
    listing = subprocess.run(
        ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
    )
    return listing.stdout.splitlines()


def sync(root: Path, values: dict[str, str], files: list[str], *, write: bool) -> Report:
    """Compare (and with ``write``, rewrite) every rule's matches against ``values``."""
    report = Report()
    candidates = sorted(set(files) - _NEVER_TOUCHED)
    texts: dict[str, str] = {}
    for rule in RULES:
        targets = rule.files if rule.files is not None else candidates
        for name in targets:
            if name in _NEVER_TOUCHED:
                continue
            if name not in texts:
                try:
                    texts[name] = (root / name).read_text(encoding="utf-8")
                except (FileNotFoundError, UnicodeDecodeError):
                    if rule.files is not None:
                        report.missing.append((name, rule.name))
                    continue
            text = texts[name]
            if rule.files is not None and not rule.pattern.search(text):
                report.missing.append((name, rule.name))
                continue
            wanted = values[rule.key]

            def replace(match: re.Match[str], name: str = name, rule: Rule = rule) -> str:
                prefix, found, suffix = match.groups()
                if found != wanted:
                    report.changes.append((name, rule.name, found, wanted))
                return f"{prefix}{wanted}{suffix}"

            texts[name] = rule.pattern.sub(replace, text)
    if write:
        for name, text in texts.items():
            path = root / name
            if path.read_text(encoding="utf-8") != text:
                path.write_text(text, encoding="utf-8")
    return report


def main(
    check: bool = typer.Option(
        False, "--check", help="Change nothing; exit 1 if a pin or default is out of date."
    ),
) -> None:
    """Sync release pins and documented defaults with pyproject.toml and app/config.py."""
    values = current_values()
    report = sync(ROOT, values, tracked_files(), write=not check)
    for name, rule in report.missing:
        console.print(
            f"[red]missing[/red] {escape(name)}: no [bold]{escape(rule)}[/bold] found; "
            "was the text reworded? Update the rule in scripts/sync_release_pins.py."
        )
    verb = "out of date" if check else "updated"
    for name, rule, old, new in report.changes:
        console.print(
            f"[yellow]{verb}[/yellow] {escape(name)} ({escape(rule)}): "
            f"{escape(old)} -> {escape(new)}"
        )
    if report.clean:
        console.print(
            f"[green]in sync[/green]: v{escape(values['version'])}, "
            f"default model {escape(values['model'])}"
        )
        return
    if check or report.missing:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    typer.run(main)
