# AI-Driven E2E Test Self-Healing Engine

Automatically repair broken Playwright E2E tests. When a UI change renames or restructures
an element, the engine diagnoses the failure, patches the broken selector/wait, re-runs the
test until it passes (or a retry cap is hit), and writes the fix back — as a local CLI or a
CI GitHub Action that opens a patch PR.

> **Scope guardrail:** the engine only fixes **failing locators and wait conditions**. It
> never touches assertions or test logic. Patches are always human-reviewable.

## How it works

Four layers drive a LangGraph repair loop:

1. **CLI core** — the single entry point (`e2e-healer`); everything, including CI, calls it.
2. **Data Preprocessor** — abstracts the raw Playwright log and the `git diff` into compact,
   hallucination-resistant context (the failing selector + the DOM attribute that changed).
3. **LangGraph agent** — `Diagnoser → Patch Generator → Test Runner`, looping via a
   conditional Router until the test passes or `max_loops` is reached.
4. **Test Runner** — runs `npx playwright test` via subprocess to validate each attempt.

```
        ┌──────────┐      ┌─────────────────┐      ┌─────────────┐
  ──▶   │ Diagnoser │ ──▶ │ Patch Generator │ ──▶  │ Test Runner │ ──┐
        └──────────┘      └─────────────────┘      └─────────────┘   │
             ▲                                                        │
             │        fail & loop_count < max                        │
             └───────────────────  Router  ◀──────────────────────────┘
                                     │ pass or loop cap
                                     ▼
                                   [End]
```

See [`docs/design.md`](docs/design.md) for the full design.

## Install

Requires Python 3.13+ and a Playwright project (Node) in your repo.

```bash
uv sync                 # or: pipx install ai-driven-e2e  (once published)
cp .env.example .env    # then set E2E_HEALER_OPENAI_API_KEY
```

## Usage (CLI)

```bash
# Heal a failing test. With no --log, the tool runs the test itself to capture the failure.
uv run e2e-healer tests/example.spec.ts

# Preview only — run the loop but write nothing:
uv run e2e-healer tests/example.spec.ts --dry-run

# Feed a pre-captured log and a PR-scoped diff (the CI path):
uv run e2e-healer tests/example.spec.ts --log playwright.log --diff-base origin/main --json
```

Exit code is `0` when the test is healed, non-zero otherwise. `--json` prints a machine-
readable `RepairSummary` to stdout (human output goes to stderr) so CI can branch on it.

## Usage (CI / GitHub Action)

Run the suite and auto-heal on failure, opening a patch PR for review. A generic wiring:

```yaml
- name: E2E self-heal
  id: heal
  uses: Lee-Dongwook/ai-driven-e2e@v0.2
  with:
    test-path: tests/example.spec.ts
    openai-api-key: ${{ secrets.OPENAI_API_KEY }}
    diff-base: ${{ github.event.pull_request.base.sha }}

- name: Open patch PR
  if: steps.heal.outputs.outcome == 'healed'
  uses: peter-evans/create-pull-request@v6
  with:
    body-path: ${{ steps.heal.outputs.summary-path }}
    branch: e2e-self-heal/${{ github.run_id }}
```

The action's `outcome` output is `passed` \| `healed` \| `unhealed`. For a project whose
Playwright suite lives in a subdirectory, pass `working-directory:`. A **runnable self-demo**
that heals this repo's own `examples/` project is in
[`ci/github-workflow.example.yml`](ci/github-workflow.example.yml) — copy it into
`.github/workflows/` and set `OPENAI_API_KEY` to activate.

## Configuration

All settings use the `E2E_HEALER_` prefix (see [`.env.example`](.env.example)):

| Variable | Default | Purpose |
| --- | --- | --- |
| `E2E_HEALER_OPENAI_API_KEY` | — | LLM provider API key |
| `E2E_HEALER_OPENAI_MODEL` | `gpt-4o-2024-08-06` | Structured-Outputs-capable model |
| `E2E_HEALER_MAX_LOOPS` | `3` | Repair loop cap |
| `E2E_HEALER_PLAYWRIGHT_CMD` | `npx playwright test` | Playwright invocation |

## Development

```bash
make install    # uv sync --extra dev
make check      # ruff + pyright
make test       # pytest
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Limitations

- Fixes selectors and waits only — never assertions or control flow.
- The JSX/TSX diff analyzer is a regex heuristic in v0.1 (tree-sitter upgrade planned).
- Healing quality depends on the LLM and the clarity of the `git diff`.

## License

[MIT](LICENSE)
