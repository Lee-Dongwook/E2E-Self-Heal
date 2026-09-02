# @e2e-healer/playwright (SPIKE)

A prototype **Playwright Reporter** that validates the integration-surface hypothesis from
[issue #299](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/299): *the adoption
barrier is the integration surface, not the Python runtime — as long as the runtime is
hidden.*

It collects failing tests during a Playwright run and shells out to the existing Python
`e2e-healer review` engine, then writes an aggregated review report. **It never patches
tests** — review-only, matching the recommended CI posture.

## How to try it (against the repo's `examples/` fixture)

Prerequisites:

```bash
# 1. Python engine available on PATH (installed once, invisible to the test author):
cd /path/to/E2E-Self-Heal && uv sync --extra dev   # provides `e2e-healer`

# 2. Example app + Playwright:
cd examples && pnpm install && pnpm exec playwright install chromium
```

Then add the reporter to `examples/playwright.config.ts` — this is the **only** config
change a test author makes:

```ts
export default defineConfig({
  // ...
  reporter: [
    ["list"],
    ["../../integrations/playwright-reporter/src/index.ts", { diffBase: "HEAD~1" }],
  ],
});
```

Run a failing scenario and let the Reporter drive the engine:

```bash
cd examples
E2E_HEALER_LLM_PROVIDER=ollama E2E_HEALER_LLM_MODEL=llama3.1 \
  pnpm exec playwright test scenarios/classname-rename
```

On failure the Reporter:

1. collects each failing test (title, source location, error),
2. writes a raw failure log to a temp file,
3. invokes `e2e-healer review <spec> --log <log> --diff-base <base> --json`,
4. aggregates the `ReviewReport`s into `e2e-healer-review.json`.

## Options

| Option | Default | Maps to |
| --- | --- | --- |
| `command` | `"e2e-healer"` | the CLI to invoke |
| `diffFile` | — | `--diff <path>` |
| `diffBase` | — | `--diff-base <ref>` |
| `outputFile` | `"e2e-healer-review.json"` | report destination |
| `resultsDir` | — | `E2E_HEALER_TEST_RESULTS_DIR` (ARIA snapshot lookup) |

## Validation runbook (the 4 criteria)

| # | Criterion | How to measure | Target |
| --- | --- | --- | --- |
| 1 | Review-only protection in <10 min | Stopwatch: fresh checkout → first `e2e-healer-review.json` | < 10:00 |
| 2 | No custom CI scripting | The `playwright.config.ts` reporter line is the only added config | no bash/`action.yml` |
| 3 | Local/offline preserved | Use `ollama` + a local model, no cloud key | run passes offline |
| 4 | Python runtime invisible | `uv sync` once, then measure install/cold-start; no Python steps in the test loop | cold start < ~30s, no manual Python |

## Known limitations (spike scope)

- Single spec-file per failure — a suite with many failures runs the engine once per file
  (the `--log`/`--diff` contract is file-scoped today).
- Error log is reconstructed from `TestError.message`/`.stack` rather than the raw
  Playwright stdout; good enough to exercise `parse_error_log`, not a fidelity guarantee.
- No `npx` bootstrap yet — the Python runtime is assumed pre-installed (that's Option A,
  to test *after* this surface is judged).

## Decision

- **All criteria pass** → keep the Python core, ship this as a native-feeling TS surface.
- **Integration surface can't hide the runtime** → escalate to a TypeScript-native engine.
