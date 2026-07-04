# Contributing

Thanks for your interest in improving the AI-Driven E2E Test Self-Healing Engine!

## Development setup

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
make install    # uv sync --extra dev + hooks
cp .env.example .env
```

## Before you open a PR

```bash
make check      # ruff (lint) + pyright (types) — must pass
make test       # pytest — must pass
make format     # ruff format
```

All CI checks (lint, format, typecheck, tests) must be green.

## Project conventions

This repo follows the rules in [`.claude/CLAUDE.md`](.claude/CLAUDE.md). Highlights:

- Imports at the top of the file; type hints on every signature.
- `structlog` for logging — lowercase_underscore event names, no f-strings, kwargs for data.
- `tenacity` for retries; `rich` for console output.
- **Guardrail:** the Patch Generator only fixes selectors/waits — never assertions or logic.
  Keep this enforced at both the prompt and the `PatchOutput` schema level.
- Repair logic lives in the CLI core; the CI wrapper only orchestrates.

## Architecture

Read [`docs/design.md`](docs/design.md) and the module layout under `app/` before making
structural changes. Preprocessors live in `app/preprocess/`, graph nodes in `app/nodes/`,
prompts in `app/prompts/`.

## Commit & PR

- Keep PRs focused; describe the change and how you verified it.
- Add or update tests under `tests/` for behavior changes.
