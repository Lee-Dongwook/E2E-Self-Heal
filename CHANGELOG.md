# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`--root` path anchor** — `e2e-healer heal` / `review` accept `--root <dir>` to anchor all
  relative-path resolution (test spec, diff file, test-results dir, and the `git diff` /
  Playwright subprocess cwd) to a project root. This lets programmatic integrators (e.g. a
  Playwright Reporter) invoke the CLI from an arbitrary working directory — the explicit
  equivalent of the shell wrapper's `cd $working-directory` (#301).

## [0.5.0] - 2026-08-29

### Added
- **Self-describing JSON contract** — every `--json` summary (`RepairSummary`, `SuiteSummary`,
  `ReviewReport`) now carries a `schema_version` (currently `"1.0"`) and a `kind`
  discriminator (`"repair"` / `"suite"` / `"review"`) so CI wrappers can detect breaking
  changes and dispatch on the payload without guessing keys (#189).
- **Healing-history memory** — successful, verified selector repairs are stored locally in
  `.e2e-healer/healing-history.json` and consulted before invoking the LLM. Matching remains
  guarded by the test path, selector context, and selector verification; pass `--no-memory`
  to bypass both lookup and storage (#208).
- **Offline token benchmark** — `e2e-healer benchmark` compares full-file and JSX-context
  diagnoser prompts against checked-in scenarios, making the context-reduction benefit
  measurable without an API key or a live Playwright run.
- **DeepSeek provider** — `E2E_HEALER_LLM_PROVIDER=deepseek` supports the DeepSeek Responses
  API with structured output. It uses `E2E_HEALER_LLM_API_KEY` or `DEEPSEEK_API_KEY` and
  defaults to `deepseek-v4-flash`.
- **DOM-diff line locations** — extracted JSX/TSX DOM changes now include the line numbers in
  the new file, improving the context supplied to diagnosis and patch generation (#224).

### Fixed
- **Settings validation** — `E2E_HEALER_MAX_LOOPS` is now bounded to `1..3` (Commandment #3:
  the Router owns termination and `loop_count` never exceeds 3), and
  `E2E_HEALER_LLM_MAX_TOKENS` / `E2E_HEALER_NVIDIA_MAX_TOKENS` require `>= 1`. Out-of-range
  values fail at config load with a clear error instead of quietly breaking the repair-loop
  budget (a stray `E2E_HEALER_MAX_LOOPS=0` previously made the Router terminate without ever
  patching) (#184).
- **Fail-fast provider configuration** — selecting a provider without a model name
  (`E2E_HEALER_LLM_MODEL`) now errors at config load rather than surfacing deep inside the
  provider SDK on the first LLM call. NVIDIA keeps its legacy default model (#184).
- **Repair guardrail coverage** — patch validation now ignores Playwright-like calls in
  comments, strings, regular-expression literals, and template-expression text; it also
  rejects edits to action option objects and preserves original input data. This closes paths
  that could otherwise allow changes beyond selectors and wait conditions (#256, #260, #261).
- **Rollback and file safety** — the original test is restored after graph exceptions, atomic
  writes preserve existing permissions and line endings, reject symlink/special-file targets
  and symlinked parents, and fsync both replacement and parent directory (#210, #257).
- **Shadow replay reliability** — replay URLs must be absolute and origin-compatible, scoring
  is averaged across matches, workspaces are cleaned up safely, and execution has a bounded
  timeout so replay cannot hang indefinitely (#213, #214).
- **Shadow snapshot confidentiality and integrity** — sensitive values are redacted before
  snapshots are persisted; snapshot identity, corruption handling, and content validation are
  hardened (#219).
- **Review and CLI outcomes** — incomplete provider reviews are reported rather than treated
  as complete, missing provider configuration exits nonzero, and failed healing no longer
  leaves a stale summary path (#221, #222).
- **Verification consistency** — every verification rollback path now restores the same
  original test state (#218).
- **Examples and integration** — the class-name rename scenario is runnable again, and the
  composite GitHub Action installs the healer from the action path with tighter consumer
  permissions (#192).

### Changed
- **Healer graph flow** — guarded healing-history lookup runs before diagnosis and can short-
  circuit LLM work only after the remembered repair passes selector verification.
- Documentation, examples, and GitHub Action smoke coverage now reflect the current CLI,
  JSON summaries, exit-code contract, and sandbox behavior.

### Security
- Shadow snapshot redaction removes sensitive headers, cookies, query values, and JSON fields
  before persisted replay data can be exposed (#213).

## [0.5.0-pre] - 2026-07-28

Preview release. Everything below is shipped and tested; the surfaces added here
(registry, notifications, selector hints, Shadow extensions) were finalized in `0.5.0`.

### Added
- **Failed Test Registry** (`app/registry.py`) — aggregate failure statistics for the heal
  loop: per-component and per-selector-pattern tracking, with categorized `FailureCause`
  (id rename, className change, text change, structural change) and `SelectorKind`
  (css id/class, role, test id, xpath, text) models (#122).
- **Slack notifications** for heal outcomes — posts a block-formatted summary to an incoming
  webhook set via `E2E_HEALER_SLACK_WEBHOOK_URL` (a no-op when unset). Retries are handled by
  tenacity and deliberately only fire on transient failures (network errors, HTTP 429/5xx);
  4xx responses fail fast instead of hammering a bad webhook (#124).
- **`e2e-healer init` readiness report** — inspects the repo (Playwright config, discovered
  spec files and their directories, `playwright` in `package.json`, configured LLM provider
  and API key) and prints a rich status table. `--scaffold` additionally writes a starter
  GitHub Actions workflow so a repo can wire the healer into CI in one step, and `--force`
  overwrites an existing one (#126, #144).
- **`--selector-hint`** on `heal` — accepts a JSON `SelectorHint` (`role` / `testid` / `text`
  / `css`, plus the original selector and a confidence score) so a human, or a DOM picker,
  can pinpoint the intended target instead of leaving it to inference (#119).
- **Framework-adaptive patch prompts** — the Patch Generator detects React / Vue / Svelte
  (from the diff's file extensions, the test's imports, and nearby `package.json`
  dependencies) and appends framework-specific selector guidance to the system prompt.
  Detection is token-aware so an unrelated dependency name can't shadow the real framework,
  and unknown stacks fall back to generic guidance. The core integrity guardrail is
  appended to, never weakened.
- **Semantic JSX chunker** (`app/preprocess/jsx_chunker.py`) — locates the failing line and
  sends only the enclosing JSX element to the LLM instead of the whole file, bounding context
  cost. Configurable via `E2E_HEALER_JSX_CHUNK_MARGIN_LINES`, with a line-window fallback
  when tree-sitter is unavailable.
- **Architecture-boundary enforcement** in the Patch Generator — `E2E_HEALER_ARCHITECTURE_ALLOW_GLOBS`
  / `E2E_HEALER_ARCHITECTURE_DENY_GLOBS` constrain which paths a generated patch may touch.
  A violation is recorded to the new `boundary_report` state field and aborts the patch
  rather than letting it leak across architectural lines.
- **Markdown report generator** (`app/core/report/generator.ts`) — renders a run summary
  (problem, before/after DOM, diagnosis, patch, pass/fail) as Markdown, with absolute-path
  redaction and code-fence escaping so report content can't break the surrounding document
  (#136).
- **Configurable test-run timeout** — `E2E_HEALER_TEST_TIMEOUT_SECONDS` (default 120) caps a
  Playwright run so a hung test (dead dev server, deadlocked `waitForSelector`) can no longer
  stall the repair loop.
- Shadow Testing extension points, behind the `I*` interfaces:
  - **HAR trace parser** (`har_parser.py`, `har_entry.py`) — build snapshots from a standard
    HTTP Archive file, alongside the existing Playwright trace parser.
  - **Content-addressed snapshot store** — payloads are stored under a SHA-256 of their
    canonical content with a small per-id ref pointing at it, so identical responses
    deduplicate to one object and snapshot sets become diffable. Implements the existing
    `ISnapshotStore` contract, so it drops in without touching call sites.
  - **Configurable miss policy** — `strict`, `lenient`, or `record-and-augment` for requests
    with no matching snapshot.
  - **Opt-in richer matching** (`MatchOptions`) — extra ignored/case-insensitive query params,
    headers promoted to hard match requirements, exact normalized-body matching, and
    order-insensitive JSON array comparison. Every field defaults to the previous behavior,
    so an unconfigured matcher is unchanged.
  - **Non-HTTP snapshots** — cookies, `localStorage`, and clock state captured from and
    restored to a Playwright browser context, so replay covers more than the network layer.
- Real **React + Vite** demo app under `examples/` with an id-rename breakage scenario, for
  reproducible end-to-end tests.
- Documentation site (Docusaurus) with GitHub Pages deploy, SEO/GEO metadata, and analytics.
- RFC for a Chrome Extension ↔ CLI bridge (DOM picker → `--selector-hint`), in
  `docs/rfc_dom_picker_cli_bridge.md` (#140).

### Changed
- Test Runner rebuilt on `subprocess.Popen` with an explicit process group (POSIX
  `start_new_session`, Windows `CREATE_NEW_PROCESS_GROUP`), so a timed-out run can reap the
  entire tree — Playwright's browser and helper descendants included — instead of orphaning
  them. A timeout surfaces as an ordinary test failure (refreshing `error_log` and
  incrementing `loop_count`) rather than an exception, keeping the graph alive.
- Patch application hardened: patch instructions are validated against the current code
  before being applied, with the outcome recorded in the new `patch_application_report`
  state field, plus line-ending preservation and a safety net for malformed edits.
- `examples/` migrated from npm to pnpm; CI pinned to Node 22+ with a matching pnpm version.
- Docs and roadmap reconciled with what actually shipped; READMEs updated.
- Reviewer auto-assignment workflow made resilient to non-collaborators.

### Fixed
- Diff/HAR robustness: HAR scalar fields are validated and compatibility edge cases handled
  before a snapshot is built.
- Snapshot store correctness: objects and refs publish atomically, a referenced hash is
  validated before it is turned into a path, snapshot-id normalization is no longer lossy,
  and unreadable objects are normalized into the store's error hierarchy instead of raising
  raw I/O errors.
- Framework detection reads `package.json` as explicit UTF-8.
- The JSX chunk margin is validated instead of trusted.
- Diagnoser LLM calls are wrapped in `try/except`; subprocess failures are caught and logged
  with `logger.exception()` rather than `logger.error()`.
- `ContentAddressedSnapshotStore` is exported from `app.shadow`.

### Security
- Browser-state snapshots hardened — cookie and storage entries returned by Playwright are
  validated on the way in, and `Secure` / `SameSite` attributes are enforced on restore so a
  replayed cookie can't be downgraded relative to how it was captured.

## [0.4.0] - 2026-07-17

### Added
- **Shadow Testing** execution mode — replay tests against captured, deterministic network
  snapshots instead of a live backend. The full pipeline (trace parser, snapshot store, mock
  injector, matcher, and runtime orchestration) is implemented and integrated into the heal
  graph via the `shadow_verifier` node, enabling fast, repeatable, side-effect-free patch
  verification before a full live test run. Activated with `--shadow`.
- Multi-provider LLM support: **OpenAI**, **Anthropic (Claude)**, and **Ollama** (local,
  offline) alongside the existing NVIDIA NIM, selected via `E2E_HEALER_LLM_PROVIDER`.
  Configuration is provider-neutral (`E2E_HEALER_LLM_*`); the standard `OPENAI_API_KEY` /
  `ANTHROPIC_API_KEY` are used as fallbacks, and Ollama needs no key. Anthropic and Ollama
  are optional extras — `ai-driven-e2e[anthropic]` / `ai-driven-e2e[ollama]` — so users who
  don't need them don't pull in the dependency.

### Changed
- LLM client rebuilt on a provider-agnostic abstraction over LangChain chat models: one
  interface for free-text and structured completion plus a factory keyed on
  `llm_provider`. Node call sites are unchanged. Structured outputs are enforced per
  provider (OpenAI/NVIDIA strict `json_schema`, Anthropic tool-use, Ollama native
  JSON-schema `format`), with the tenacity retry and Patch Generator feedback loop
  preserved so a flaky JSON response never crashes the run.
- Documentation: README gains a provider matrix and per-provider setup blocks;
  `.env.example` shows every provider.

## [0.3.0] - 2026-07-11

### Changed
- Diff parsing rewritten on a tree-sitter AST: the JSX/TSX diff analyzer now walks the
  parsed syntax tree instead of matching regexes, producing more accurate and robust
  before/after DOM node extraction. Added the tree-sitter dependencies and expanded the
  diff-analyzer test coverage accordingly.

## [0.2.2] - 2026-07-11

### Added
- PR review-bot mode: `e2e-healer` can attach to a repository and review pull requests
  (new Reviewer node, prompts, and structured schemas), surfaced through the CLI, the
  composite Action (`action.yml`), and an example workflow (`ci/github-review-bot.example.yml`).
- `classname-scenario` example: a broken-selector demo covering a className rename.
- Japanese README translation (`README.ja.md`).

### Changed
- CI: added coverage reporting and an examples smoke-test job.
- Tooling: expanded ruff lint/format config, added `.editorconfig` and pre-commit hooks,
  and applied import sorting/formatting across the codebase.
- Added an auto-assign-reviewer workflow.

### Fixed
- Added `None` guards to the preprocessors (error-log parser, diff AST analyzer, aria
  snapshot) and expanded their test coverage.

## [0.2.0] - 2026-07-04

### Added
- Suite mode: `e2e-healer` with no path (or a directory) runs the whole Playwright suite,
  then heals every failing test file and emits an aggregate `SuiteSummary` (exit 0 only if
  all are healed). Single-file usage is unchanged.

## [0.1.0] - 2026-07-04

### Added
- CLI core (`e2e-healer`) that heals a failing Playwright test end-to-end: preprocess
  (error log + JSX/TSX diff), LangGraph loop (Diagnoser → Patch Generator → Selector
  Verifier → Test Runner), and a Router with a loop cap.
- Selector Verifier node: verifies patched selectors against the live DOM via a
  Node/Playwright helper; hallucinated/ambiguous selectors are reverted and re-patched
  before a full test run. Config via `E2E_HEALER_VERIFY_SELECTORS` / `E2E_HEALER_APP_URL`
  and the `--app-url` flag / `app-url` action input.
- Self-run failure capture when `--log` is omitted; `--dry-run`, `--diff-base`, `--json`.
- Atomic in-place writes with restore-on-give-up.
- Reusable composite GitHub Action (`action.yml`) + example patch-PR workflow.
- Unit and mocked end-to-end tests; repo CI (lint, format, typecheck, test).

### Changed
- LLM provider migrated from OpenAI to NVIDIA NIM (`openai/gpt-oss-120b`) via the
  OpenAI-compatible endpoint; Structured Outputs guardrail retained.

[Unreleased]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.5-pre...v0.5.0
[0.5.0-pre]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.4.0...v0.5-pre
[0.4.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.2.0...v0.2.2
[0.2.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/releases/tag/v0.1.0
