# AI-Driven E2E Test Self-Healing Engine

<!-- language: **English** · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-CN.md) -->

**English** · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-CN.md)

[![CI](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml/badge.svg)](https://github.com/Lee-Dongwook/E2E-Self-Heal/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A **self-healing** engine that automatically repairs broken **Playwright** E2E tests with an
**AI agent** built on **LangGraph**. When a UI change renames or restructures
an element and a test's selector breaks, the engine diagnoses the failure, patches the
broken selector/wait, **verifies the new selector against the live DOM**, then re-runs the
test until it passes (or a retry cap is hit) and writes the fix back — as a local **CLI** or
a **CI GitHub Action** that opens a patch PR.

> **Scope guardrail:** the engine only fixes **failing locators and wait conditions**. It
> never touches assertions or test logic, and every patch stays human-reviewable.

### Two modes: **heal** and **review**

Auto-healing patches the _test_. That is fast, but on its own it can look like papering over
a real problem — the source that broke the selector. So the engine offers a second mode:

- **`heal`** (default) — patch the broken selector/wait and re-run until green. Best when the
  UI change is intentional and the test simply needs to catch up.
- **`review`** — diagnose _why_ the selector broke and post **source-level suggestions** as
  **inline PR comments** (e.g. "this `className` rename broke `#cta`; add a stable
  `data-testid` or use `getByRole`"). It **never edits the test** — it advises the fix at the
  source and pushes teams toward resilient, accessibility-first selectors.

Same diagnosis engine, two outputs: a patch, or a review. Pick per-project or per-PR.

![e2e-healer demo — diagnose, verify against the live DOM, re-run, fixed](https://raw.githubusercontent.com/Lee-Dongwook/E2E-Self-Heal/main/docs/demo.gif)

## How it works

Four layers drive a LangGraph repair loop:

1. **CLI core** — the single entry point (`e2e-healer`); everything, including CI, calls it.
2. **Data Preprocessor** — abstracts the raw Playwright log and the `git diff` into compact,
   hallucination-resistant context (the failing selector + the DOM attribute that changed).
3. **LangGraph agent** — `Diagnoser → Patch Generator → Selector Verifier → Test Runner`,
   looping via a conditional Router until the test passes or `max_loops` is reached.
4. **Selector Verifier** — checks each patched selector against the real page DOM so it
   resolves to **exactly one** element (Node/Playwright helper). Hallucinated (0 matches) or
   ambiguous (>1) selectors are reverted and re-patched _before_ a full test run.
5. **Test Runner** — runs `npx playwright test` via subprocess to validate each attempt.

```
   ┌──────────┐    ┌─────────────────┐    ┌───────────────────┐    ┌─────────────┐
──▶│ Diagnoser│──▶ │ Patch Generator │──▶ │ Selector Verifier │─┬─▶│ Test Runner │──┐
   └──────────┘    └─────────────────┘    └───────────────────┘ │  └─────────────┘  │
        ▲                   ▲  verify fail (0/2+ match) → repatch ┘                   │
        │                   └───────────────────────────────────────────────────────┘│
        │                          fail & loop_count < max                            │
        └───────────────────────────────  Router  ◀───────────────────────────────────┘
                                            │ pass or loop cap
                                            ▼
                                          [End]
```

> The Selector Verifier **skips** gracefully (loop proceeds unverified) when
> `E2E_HEALER_APP_URL` is empty or the page is unreachable (e.g. Node/Playwright not
> installed) — tooling problems never block a heal.

See [`docs/design.md`](docs/design.md) for the full design, and
[`docs/shadow-testing.md`](docs/shadow-testing.md) for the planned Shadow Testing pipeline.

## Usage (CI / GitHub Action)

The flagship workflow: run your suite and auto-heal on failure, opening a patch PR for review:

```yaml
- name: E2E self-heal
  id: heal
  uses: Lee-Dongwook/E2E-Self-Heal@v0.4.0
  with:
      test-path: tests/example.spec.ts
      nvidia-api-key: ${{ secrets.NVIDIA_API_KEY }}
      diff-base: ${{ github.event.pull_request.base.sha }}
      app-url: http://localhost:4173 # optional: enables live selector verification

- name: Open patch PR
  if: steps.heal.outputs.outcome == 'healed'
  uses: peter-evans/create-pull-request@v6
  with:
      body-path: ${{ steps.heal.outputs.summary-path }}
      branch: e2e-self-heal/${{ github.run_id }}
```

The action's `outcome` output is `passed` \| `healed` \| `unhealed` (heal mode) or `reviewed`
(review mode). For a Playwright suite in a subdirectory, pass `working-directory:`. A
**runnable self-demo** that heals this repo's own `examples/` project lives in
[`ci/github-workflow.example.yml`](ci/github-workflow.example.yml).

To run as a **PR review bot** instead, pass `mode: review` and post the findings as inline PR
comments — a ready-to-copy workflow lives in
[`ci/github-review-bot.example.yml`](ci/github-review-bot.example.yml):

```yaml
- name: E2E review
  id: review
  uses: Lee-Dongwook/E2E-Self-Heal@v0.4.0
  with:
      mode: review
      test-path: tests/example.spec.ts
      nvidia-api-key: ${{ secrets.NVIDIA_API_KEY }}
      diff-base: ${{ github.event.pull_request.base.sha }}
# then read steps.review.outputs.review-path and post inline comments (see the example)
```

## Demo (verified end-to-end)

The [`examples/`](examples/) project reproduces a real break: the page's button id was
renamed `submit-btn` → `submit`, so `example.spec.ts` times out. Running the healer against
it (with a live NVIDIA key) produces:

```text
diagnoser_finished
patch_generator_finished        instruction_count=1
selector_verify_started         selector_count=1 url=http://localhost:4173
selector_verify_passed          counts={'#submit': 1}
test_runner_passed              loop_count=0
fixed after 0 loop(s)
```

```diff
- await page.click("#submit-btn");
+ await page.click("#submit");        # assertion on "Thanks!" left untouched
```

Reproduce it yourself: see [`examples/README.md`](examples/README.md).

## In practice

A real run against a landing-page CTA test. A UI refactor renamed the button id
`#enter-demo-btn` → `#demo-cta-btn`, so `demo-cta.spec.ts` started failing on
`locator.click`. Pointed at the file, the engine diagnosed the break against the `git diff`,
patched **only the broken selector** (leaving the `toHaveURL` assertion untouched), re-ran
the suite, and passed on the first attempt — end to end on NVIDIA NIM
(`integrate.api.nvidia.com`, `openai/gpt-oss-120b`):

![Real-world run: diagnose the renamed CTA selector, patch it, re-run, fixed after 0 loops](https://raw.githubusercontent.com/Lee-Dongwook/E2E-Self-Heal/main/docs/usecase-demo-cta.png)

```text
playwright_run_finished     passed=False                    # original selector times out
diagnoser_started           loop_count=0
diagnoser_finished
patch_generator_finished    instruction_count=1
test_runner_started
playwright_run_finished     passed=True
repair_run_finished         is_success=True loop_count=0
fixed after 0 loop(s)
```

```diff
  test('guest enters the demo workspace from the landing CTA', async ({ page }) => {
    await page.goto('/')
-   await page.click('#enter-demo-btn')
+   await page.click('#demo-cta-btn')
    await expect(page).toHaveURL(/\/w\//)   // assertion left untouched
  })
```

## Install

Requires Python 3.13+ and a Playwright project (Node) in your repo.

**Recommended — one-line global install:**

```bash
pipx install ai-driven-e2e
# or, before PyPI release:
uv tool install git+https://github.com/Lee-Dongwook/E2E-Self-Heal.git
```

Then in any Playwright project:

```bash
cp .env.example .env    # set E2E_HEALER_LLM_API_KEY (or your provider's key)
e2e-healer tests/login.spec.ts
```

Works with **NVIDIA NIM (default), OpenAI, Anthropic (Claude), or a local Ollama model** —
see [Configuration](#configuration) to pick one. For the default, get a free NVIDIA NIM API
key at [build.nvidia.com](https://build.nvidia.com/) (default model `openai/gpt-oss-120b`).

<details>
<summary>Development install from a local clone</summary>

```bash
git clone https://github.com/Lee-Dongwook/E2E-Self-Heal.git
cd E2E-Self-Heal
uv sync --extra dev
uv tool install --force .    # global `e2e-healer` from this checkout
```

Re-run `uv tool install --force .` after pulling changes.

</details>

## Usage (CLI)

```bash
# Heal the WHOLE suite — run every test, then repair each failing file (aggregate summary):
uv run e2e-healer

# Heal a single failing test (with no --log, the tool runs it to capture the failure):
uv run e2e-healer tests/example.spec.ts

# Preview only — run the loop but write nothing:
uv run e2e-healer tests/example.spec.ts --dry-run

# Feed a pre-captured log and a PR-scoped diff (the CI path):
uv run e2e-healer tests/example.spec.ts --log playwright.log --diff-base origin/main --json

# Enable live-DOM selector verification against a running app:
uv run e2e-healer tests/example.spec.ts --app-url http://localhost:4173

# Review mode — suggest source-level fixes instead of patching (never edits the test):
uv run e2e-healer review tests/example.spec.ts --log playwright.log --diff-base origin/main --json
```

Exit code is `0` when the test is healed, non-zero otherwise. `--json` prints a
machine-readable `RepairSummary` to stdout (human output goes to stderr) so CI can branch
on it. `e2e-healer <path>` is shorthand for `e2e-healer heal <path>`; `review` is a separate
subcommand that emits a `ReviewReport` (findings anchored to the changed source line) and
always exits `0` — the CI wrapper branches on `has_findings`.

## Configuration

All settings use the `E2E_HEALER_` prefix (see [`.env.example`](.env.example)). LLM
configuration is **provider-neutral**: pick a backend with `E2E_HEALER_LLM_PROVIDER` and
configure it via the generic `LLM_*` vars. The legacy `NVIDIA_*` vars still work — when
the provider is `nvidia` they are folded into the generic ones, so existing setups need no
changes.

### Provider matrix

| Provider    | Install                        | Required env                                              | Structured outputs            | Notes                                                                 |
| ----------- | ------------------------------ | -------------------------------------------------------- | ----------------------------- | --------------------------------------------------------------------- |
| `nvidia`    | built-in (default)             | `E2E_HEALER_LLM_API_KEY` (or legacy `NVIDIA_API_KEY`)    | strict `json_schema`          | OpenAI-compatible NIM endpoint; default `openai/gpt-oss-120b`.        |
| `openai`    | built-in                       | `E2E_HEALER_LLM_API_KEY` **or** `OPENAI_API_KEY`         | strict `json_schema` (native) | Set `LLM_BASE_URL` for Azure / OpenAI-compatible endpoints.           |
| `anthropic` | `ai-driven-e2e[anthropic]`     | `E2E_HEALER_LLM_API_KEY` **or** `ANTHROPIC_API_KEY`      | tool-use                      | No OpenAI `response_format`; schema enforced via Claude tool-use.     |
| `ollama`    | `ai-driven-e2e[ollama]`        | none (local)                                             | native JSON-schema `format`   | Fully offline; smaller models are less reliable at strict JSON.       |

All providers read the generic `E2E_HEALER_LLM_MODEL` / `E2E_HEALER_LLM_MAX_TOKENS` /
`E2E_HEALER_LLM_BASE_URL`. On a structured-output parse failure the engine retries
(tenacity) and then falls back to the Patch Generator feedback loop, so a flaky JSON
response never crashes the run.

### Using OpenAI

Set the provider to `openai` and supply a key — either the generic `E2E_HEALER_LLM_API_KEY`
or an existing standard `OPENAI_API_KEY` (used as a fallback). Patch/review use OpenAI's
native strict Structured Outputs. Point `E2E_HEALER_LLM_BASE_URL` at an Azure or
OpenAI-compatible endpoint to override the default.

```bash
E2E_HEALER_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...            # or E2E_HEALER_LLM_API_KEY=sk-...
E2E_HEALER_LLM_MODEL=gpt-4o-mini
```

### Using Anthropic (Claude)

Anthropic is an **optional** dependency — install the extra so non-Anthropic users don't
pull it in:

```bash
pip install "ai-driven-e2e[anthropic]"   # or: uv sync --extra anthropic
```

Then set the provider and a key — either `E2E_HEALER_LLM_API_KEY` or an existing standard
`ANTHROPIC_API_KEY` (fallback). Claude has no OpenAI-style `response_format`, so
`PatchOutput`/`ReviewOutput` are enforced via tool-use.

```bash
E2E_HEALER_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...     # or E2E_HEALER_LLM_API_KEY=sk-ant-...
E2E_HEALER_LLM_MODEL=claude-sonnet-4-6
```

### Using Ollama (local, offline)

Run fully offline with **no API key**. Ollama is an optional dependency:

```bash
pip install "ai-driven-e2e[ollama]"      # or: uv sync --extra ollama
```

Start Ollama and pull a model, then point the provider at it. Structured outputs use
Ollama's **native JSON-schema `format`**, so pick a model that handles structured output
well — recommended: `llama3.1`, `qwen2.5-coder`, or `mistral-nemo`. Smaller/older models are
less reliable at strict JSON; on a parse failure the engine retries and falls back to the
Patch Generator feedback loop.

```bash
E2E_HEALER_LLM_PROVIDER=ollama
E2E_HEALER_LLM_MODEL=llama3.1
# E2E_HEALER_LLM_BASE_URL=http://localhost:11434   # default; override for a remote host
```

| Variable                       | Default                               | Purpose                                        |
| ------------------------------ | ------------------------------------- | ---------------------------------------------- |
| `E2E_HEALER_LLM_PROVIDER`      | `nvidia`                              | LLM backend: `nvidia`, `openai`, `anthropic`, `ollama` |
| `E2E_HEALER_LLM_API_KEY`       | —                                     | API key for the selected provider              |
| `E2E_HEALER_LLM_BASE_URL`      | —                                     | OpenAI-compatible endpoint (empty = SDK default) |
| `E2E_HEALER_LLM_MODEL`         | —                                     | Structured-Outputs-capable model               |
| `E2E_HEALER_LLM_MAX_TOKENS`    | `4096`                                | Completion token cap (headroom for reasoning)  |
| `E2E_HEALER_NVIDIA_API_KEY`    | —                                     | NVIDIA NIM API key (legacy; maps to `LLM_API_KEY`) |
| `E2E_HEALER_NVIDIA_BASE_URL`   | `https://integrate.api.nvidia.com/v1` | OpenAI-compatible endpoint (legacy)            |
| `E2E_HEALER_NVIDIA_MODEL`      | `openai/gpt-oss-120b`                 | Structured-Outputs-capable model (legacy)      |
| `E2E_HEALER_NVIDIA_MAX_TOKENS` | `4096`                                | Completion token cap (legacy)                  |
| `E2E_HEALER_MAX_LOOPS`         | `3`                                   | Repair loop cap                                |
| `E2E_HEALER_PLAYWRIGHT_CMD`    | `npx playwright test`                 | Playwright invocation                          |
| `E2E_HEALER_VERIFY_SELECTORS`  | `true`                                | Toggle live-DOM selector verification          |
| `E2E_HEALER_APP_URL`           | —                                     | URL the Selector Verifier loads (empty = skip) |
| `E2E_HEALER_NODE_CMD`          | `node`                                | Node executable for the verifier               |
| `E2E_HEALER_SANDBOX_MODE`      | `relaxed`                             | `strict`, `relaxed`, or `off`                  |
| `E2E_HEALER_WORKSPACE_ROOT`    | `.`                                   | Root for strict path checks                    |
| `E2E_HEALER_WRITE_GLOBS`       | `*.spec.js,...`                       | Writable test-file globs                       |
| `E2E_HEALER_DENY_GLOBS`        | `.env,.git/**,...`                    | Paths blocked by the sandbox                   |
| `E2E_HEALER_ALLOW_TEMP_HELPER` | `true`                                | Permit selector verifier helper file           |

> The `--app-url` CLI flag overrides `E2E_HEALER_APP_URL`. To actually run selector
> verification locally, the Playwright project needs browsers installed
> (`npm install && npx playwright install`).


## Development

```bash
make install    # uv sync --extra dev
make check      # ruff + pyright
make test       # pytest
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Documentation site

The `docs-site/` directory contains our [Docusaurus](https://docusaurus.io/) documentation site — this is where frontend contributors can help.

To run it locally:

```bash
cd docs-site
npm install
npm run start      # dev server with hot reload
npm run build       # production build
```

Picking up a docs-site component issue? Read [`docs-site/DESIGN.md`](docs-site/DESIGN.md) first — it's the locked design spec (tokens, layout, dark-mode rules) all components must follow.

## Contributing



Contributions of every size are welcome — bug reports, docs, tests, or code. Start with
[`CONTRIBUTING.md`](CONTRIBUTING.md), then browse
[**good first issues**](https://github.com/Lee-Dongwook/E2E-Self-Heal/labels/good%20first%20issue)
and [**help wanted**](https://github.com/Lee-Dongwook/E2E-Self-Heal/labels/help%20wanted).

**🙋 We're actively looking for contributors on:**

- [#3 — Build a real React + Vite frontend demo environment](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/3) for the Playwright examples

See the [**v0.3 roadmap**](https://github.com/Lee-Dongwook/E2E-Self-Heal/issues/9) for the
bigger picture. New to the project? Comment on an issue to claim it — we're happy to help.

## Limitations

- Fixes selectors and waits only — never assertions or control flow.
- The Selector Verifier checks the **entry-page state** at `APP_URL` in v1. Elements that
  only appear after clicks/navigation aren't verified here; the Test Runner remains the final
  arbiter (failure-time snapshot capture is planned).
- Healing quality depends on the LLM and the clarity of the `git diff`.

## License

[MIT](LICENSE)
