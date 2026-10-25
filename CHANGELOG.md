# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-10-25

The first release whose safety gates are enforced on a corpus large enough to mean something:
44 runnable scenarios, 20 of them product regressions paired with a drift twin. The release run
(`safety-benchmark --apply-patches --jobs 4 --enforce-gates`, Claude Sonnet 4.6) scored all 44 as
labeled: `false_green_rate` 0% (n=20), `refusal_accuracy` 100% (n=22), `incorrect_refusal_rate` 0%
(n=22), `error_count` 0.

### Known issues
- With `deepseek-v4-flash`, a run can occasionally end as `provider_error` because the model
  returns an empty answer on every retry, most often on long product-regression diagnoses. A
  higher `E2E_HEALER_LLM_MAX_TOKENS` (for example `16384`) appeared to help in a small test.

### Added
- **`safety-benchmark --jobs N` runs scenarios in parallel, with live progress.** Scenarios
  share one demo app, so they ran one after another and a full corpus took about 29 minutes,
  mostly Playwright rebuilding the app and model latency. With `--jobs N` each of `N` worker
  processes gets its own copy of the Playwright project in a throwaway git repository and its
  own preview port, and runs the same executor as a sequential run. A corpus whose patches reach
  outside the project is refused up front (exit `2`), and worker output goes to per-worker log
  files. The full 43-scenario run with `deepseek-v4-flash` took 8 min 15 s at `--jobs 4` against
  about 29 min sequentially, with 43 scored and 0 errors. Every run now shows a progress bar (done,
  elapsed, ETA) and one line per finished scenario on stderr. The CI workflow uses `--jobs 4`.
- **The safety corpus is large enough to enforce the release gates** (#383). The corpus grew from
  12 scenarios to 44 (43 runnable), so every rate gate now has its 20 distinct scenarios and is
  enforced on a full run instead of reported as `insufficient_sample`. Four routed demo-app pages
  (`#/catalog`, `#/checkout`, `#/settings`, `#/account`) carry four drift/regression pairs each:
  the drift patch renames a selector and should be repaired, and the regression patch makes the
  same rename and also breaks the asserted behavior, which should be refused. Renames (id, class,
  `data-testid`, `name`) and breaks (deleted handler, wrong computation, wrong value or field,
  wrong event, broken guard, inverted condition, output detached from state) vary across the
  pairs, so the twenty regressions are not one mutation repeated. Both twins ship the identical
  spec and the same failing selector, so the gates measure discrimination rather than how easy the
  corpus is. New tests fail if the checked-in corpus can no longer enforce every rate gate or if a
  drift scenario has no regression twin. The hash-routed `App.tsx` keeps `/` on the signup page,
  and the demo port moved to a validated `E2E_DEMO_PORT` so worktrees can run the suite
  concurrently; the CI timeout is 120 minutes for the larger corpus. `wrong-base-url` now points
  at port 47, which nothing serves, instead of 4174, which a second checkout or the demo page may.
  One model-backed run over the new corpus (`deepseek-v4-flash`) scored 43 scenarios with no
  errors and enforced every gate: `false_green_rate` 0% (n=20) and `incorrect_refusal_rate` 9.5%
  (n=21) pass, and `refusal_accuracy` 87.5% (n=24) **fails** its 95% floor. The misses are
  `account-avatar-drift` (refused on `guardrail_violation` for a multi-line `setInputFiles` call),
  `settings-notifications-drift` (refused on `insufficient_evidence` for an id replaced by `name`)
  and the existing `split-submit` (repaired, expected refuse).
- **The safety benchmark now states each gate's shortfall** (#383). Every rate gate reported
  `insufficient_sample` and nothing said how far a population was from the 20 distinct scenarios
  it needs, so a gate that could never be enforced read like one that passed. `GateResult` gains
  `scenarios_needed` (defaulted, so older reports and baselines still load), the **Release gates**
  table gains a **Needs** column, and a run prints one line on stderr naming the gates that are
  still unenforced with the largest shortfall first. `error_count` is always `0`, since it has no
  sample minimum.
- **`--repeat N` for the safety benchmark** (#325). Model output varies between runs, so one run
  per scenario can move a rate by chance (`split-submit` was repaired once and refused twice by the
  same model). `e2e-healer safety-benchmark --repeat 3` runs every scenario three times, in full
  passes, and the report shows how often each scenario ended each way, which refusal reasons
  occurred, and which scenarios were unstable. Rates are fractions of runs. The gate sample still
  counts distinct scenarios, because repeating a scenario adds no coverage, and a false green in any
  single run fails the false-green gate. The JSON gains `run`, `repeats` and `scenarios`; older
  reports and baselines still load, and a single-run report is unchanged.
- **JSON output reference** (#293). A new docs page documents every field of the schema `2.1`
  `--json` contract (`RepairSummary`, `RefusalReport`, `SuiteSummary`, `ReviewReport` and every
  nested object), when a document is emitted and with which exit code, and each `RefusalReason`.
  Its examples are payloads captured from real CLI runs instead of hand-written ones, and a test
  fails when a field or a captured payload drifts from the models in `app/schemas.py`.
- **Labeled mutation corpus and release gates for the safety benchmark** (#281). The corpus now
  covers all six mutation classes (12 scenarios), including `ambiguous`, `environment` and four
  `product_regression` cases, two of which look the same from the diff as a drift scenario
  (`id-rename-submit-dropped`, `cta-rename-handler-dropped`). Every scenario folder must carry
  a valid `meta.json`; a missing file, unknown class or key, or empty rationale fails at load time
  (exit `2`) instead of being skipped. The token benchmark reads the same corpus, with unchanged
  numbers for the existing scenarios (#289). A new `refusal_accuracy` metric and a **Release
  gates** table (false-green 0%, refusal accuracy ≥ 95%, incorrect refusals ≤ 20%, no errors; rate
  gates enforced only from 20 scenarios) are added to the report, and `--enforce-gates` exits `1`
  when one fails. Gates block a release, never a merge (#292). The shadow replay spec moved from
  `examples/scenarios/` to `examples/shadow-replay/`, since it is not a mutation scenario.
- **Safety benchmark report and non-blocking CI job** — `e2e-healer safety-benchmark` now
  reports results per mutation class (all six classes are listed, so coverage gaps are visible),
  the number of scenarios behind each rate, total and per-class latency, and mean repair
  attempts (candidates that proposed a change; a model that declines to patch reports 0) and,
  separately, how many times it declined to patch.
  `--output` writes the diffable JSON report, `--markdown` a summary, and `--baseline`
  compares against a previous run (outcome flips, percentage-point movement, latency and
  attempts). `--apply-patches` breaks the demo app with each scenario's `change.patch` and always
  reverses it, which the runner previously left to the caller, so on a clean checkout every
  scenario used to report `error`. A scenario whose change is already present (the demo app
  ships `classname-rename` pre-broken) runs as-is and the app is left as found. A new
  `safety-benchmark` workflow publishes the report as a step summary and artifact. It is
  `continue-on-error`, read-only, and skipped when the model key is unavailable (for example on
  fork pull requests). Model cost in dollars is not measured yet and is reported as such, never
  as zero (#291).
- `runnable: false` in a scenario's `meta.json` marks a labeled static fixture that is not a
  Playwright test. It is listed as `skipped` and excluded from every rate. The `jsx-context`
  token-benchmark fixture is marked this way; it would otherwise have been scored as a failed
  repair (#291).

### Fixed
- **A test whose element became several look-alikes is refused, not guessed** (#388). On
  `split-submit` one `Submit` button becomes "Save draft" and "Publish"; either makes the test
  pass, so the model picked one and the run read as a repair. A deterministic check
  (`app/preprocess/ambiguous_target.py`) now refuses as `ambiguous_target` before any model call
  when the diff removes the element the failing locator named (by `id`, class, `data-testid` or
  `name`) and adds two or more elements of the same tag and `type` in the same file. A
  `--selector-hint` disables it, and role or text locators never trigger it. The refusal spends no
  loop and writes nothing; the added elements are already in `evidence.dom_diff_context`, and the
  log event `ambiguous_target_detected` names their selectors. No JSON field changed. Across the
  corpus it fires for `split-submit` only, and a new guard scenario,
  `submit-rename-reset-added` (Submit renamed with a Reset button added beside it), must still be
  repaired.
  Two full `deepseek-v4-flash` runs at `--jobs 4` (44 scored each) refused `split-submit` as
  `ambiguous_target` with no model call (about 7 s) and repaired the guard scenario. The second run
  ended with every scenario as labeled: `refusal_accuracy` 100% (n=22), `incorrect_refusal_rate`
  0% (n=22), `false_green_rate` 0% (n=20), 0 errors. The first run also scored every scenario as
  labeled but one: the model returned unparseable structured output three times in a row on
  `catalog-search-regression`, which is correctly scored as an `error` and fails `error_count`.
  That is model output, not `--jobs`: the logs show no rate limiting, and the sequential runs of
  #383 had as many unparseable answers (26 against 24) and four exhausted patch retries, which
  happened to recover on a later loop.
- **A late-rendering element gets a longer wait, not a guessed selector** (#388). On
  `delayed-cta` the CTA renders four seconds after load, so the 3 s click times out and the
  failure snapshot has no button. The diff abstraction keeps tags and attributes only, so nothing
  told the model the `.cta-primary` class still existed, and it swapped in whatever was on the
  page (`role=region[name="Get started"]`) or declined as a product regression: 1 repair in 4 runs,
  and that one rewrote the selector too. A new deterministic check
  (`app/preprocess/selector_diff_check.py`) reports when no line removed by the diff names anything
  in the failing locator; the Diagnoser and Patch Generator prompts then state that the selector
  was not renamed, so the fix for a timed-out action is its timeout option. The check is
  conservative: a rename always removes a line holding the old name, so it never fires for one.
  On the corpus it fires for `delayed-cta` and `product-regression` only. `delayed-cta` now
  repairs as `page.click(".cta-primary", { timeout: 15000 })` in 6 of 6 runs, and
  `product-regression` is still refused in 6 of 6.
  A full `deepseek-v4-flash` run over the corpus (43 scored, 0 errors) passes every release gate
  for the first time: `false_green_rate` 0% (n=20), `refusal_accuracy` 95.5% (n=22) and
  `incorrect_refusal_rate` 0% (n=21). The one miss left was `split-submit` (ambiguous, repaired),
  fixed by the ambiguous-target check above.
- **Selector repairs on multi-line Playwright calls are no longer refused** (#388). The
  action-argument gate masked selectors on the edited line alone, so a call such as
  `page.setInputFiles("#avatar", {` with its payload on later lines never matched and every
  selector-only edit was rejected as changing another argument, ending in `guardrail_violation`.
  The gate now compares the whole original and patched source; changes to any other argument are
  still rejected.
- **Attribute edits inside multi-line JSX tags reach the model** (#388). `analyze_diff` parsed
  each run of changed lines on its own, so renaming `id=` on the line below `<input` produced no
  DOM change at all. Such a run is now widened to the whole opening tag (to its closing line, or a
  synthetic close at the hunk edge); other context lines are still not parsed, so unchanged
  neighbours are not reported.
- **The failure-time page snapshot survives truncated result directories** (#388). Playwright
  shortens long `test-results/` names, which then no longer contain the spec name, and 16 of 43
  benchmark scenarios ran without a snapshot. The context is now matched on the test location
  recorded in `error-context.md`, with directory-name matching kept for artifacts that lack it.
  With all three fixes, a `deepseek-v4-flash` run over the 44-scenario corpus repairs both drifts
  it refused before (each also repaired 3 of 3 in a repeat run) and finds every failure snapshot.
  The remaining `delayed-cta` instability is fixed by the selector diff check above; the full run
  after it is reported there.
- **A declined patch is a signal, not a reason to re-run the test** (#330). When the model
  proposes no change it now gets one structured verdict on its own diagnosis: `selector_drift`,
  `behaviour_change` or `unclear`. `behaviour_change` refuses immediately as
  `likely_product_regression`, a reason that was defined but emitted by nothing, so a product
  regression is reported for what it is after one cycle instead of three. The other two verdicts
  keep the full attempt budget, and the verdict is asked at most once per run because a decline
  leaves every later cycle's prompt unchanged. The verdict is internal, so the `--json` contract
  changes only in the reason a refusal can carry.
- The safety benchmark now gives the repair graph Playwright's failure-time page snapshot, as
  `e2e-healer heal` does. It passed an empty snapshot, so a label change was invisible to the
  model (the diff analyzer records attribute changes, not text) and `label-rename` could never be
  repaired in the benchmark although the CLI repairs it (#328).
- Shadow replay no longer drops repeated HTTP headers when parsing a HAR or Playwright trace.
  Each repeat used to overwrite the one before, so a response with two `Set-Cookie` headers
  kept only the last. Repeats are now folded the way Playwright represents them: `Set-Cookie`
  values are joined with a newline and any other repeated header with `, `, grouped
  case-insensitively with the first spelling kept. The snapshot schema is unchanged, so saved
  snapshots load as before (#226).
- `classname-rename`'s `meta.json` named `.submit-btn` as the failing selector; its spec clicks
  `.cta-button`. The label now matches (#291).
- The safety benchmark scored a model-provider failure (for example unparseable structured
  output on every retry, refusal reason `provider_error`) as a refusal. On a scenario labeled
  `refuse` that counted as a correct refusal and hid the outage. It is now an `error`: it fails
  the `error_count` gate and is outside the refusal-accuracy population (#326).

### Changed
- **Learning never lowers the refusal bar is now a stated, tested invariant** (#308). Healing
  history, and any learned rule or policy setting added later, may reorder candidates or save a
  model call but never skip, relax or replace a refusal check or verification step. It is written
  down in `.claude/CLAUDE.md`, `CONTRIBUTING.md` and the README, the pull request template asks
  every learning or policy change to confirm it, and a test shows that a matching history record
  cannot pre-empt the ambiguous-target refusal: the run is refused before history is consulted,
  and the file is left as it was.
- **Structural AST lock on every patch** — the Patch Generator and the healing-history memory
  lookup now verify each candidate against the original file with the tree-sitter AST lock,
  after the existing per-line scope gates. Only locator strings, timeouts, and wait-method names
  may differ; any other structural change, or a file that does not parse, is rejected fail-closed
  as a `guardrail_violation`. A Patch Generator rejection is fed back to the generator and
  **spends a loop**, so repeated violations stop at `max_loops` and end in a `RefusalReport`.
  A rejected history candidate is treated as a cache miss: it is recorded as a rejected
  candidate, does not increment the loop count, and falls through to the LLM path (#286).
- The AST lock now treats the first argument of a `page.<action>(selector, ...)` call (`click`,
  `fill`, `waitForSelector`, ...) as an editable selector, matching the existing scope gate.
  Values, keys, options, and non-`page` receivers stay locked (#286).

### Removed
- Code nothing ran, found in a dead-code sweep:
  - The failure-cause aggregation in `app/registry.py` (`FailureCause`, `HealingRecord`,
    `aggregate_failure_stats` and its stats models). Nothing ever produced a `HealingRecord`.
    `SelectorKind` and `classify_selector_kind` stay, because the healing-history store uses them.
  - The TypeScript Markdown report generator in `app/core/report/` and the root `package.json`
    that existed only to run its Vitest test. No CLI command, Action step or workflow called it.
  - Unused internals: `selector_verifier._revert` (rollback writes the saved code directly),
    `PreparedEvidence.redaction_applied` (callers read `redaction_count`),
    `PlaywrightTraceParser._headers_to_dict` and the unreferenced `SnapshotMetadata` model.
  - The default Docusaurus scaffold left in the docs site: the unused `HomepageFeatures`
    component, its illustrations, `docusaurus.png` and the unlisted `/markdown-page` page.
  - The product dashboard's fallback to `/api/info`, an endpoint the API never had.
- Tests that checked nothing a user or CI depends on (#395): exact log, help and prompt prose, mocks
  echoing their own arguments, incidental defaults, bare `isinstance` checks and same-branch
  duplicates. Near-identical cases became parametrized tables, and test files covering the same
  module were merged (`test_patch_apply.py` into `test_patch_generator.py`,
  `test_ast_lock_corpus.py` into `test_ast_lock.py`, the three snapshot-store files into
  `test_snapshot_store.py`, `test_aria_snapshot_identity.py` into `test_aria_snapshot.py`,
  `test_selector_check_concurrent.py` into `test_selector_check.py`). The suite went from 65 test
  files and 1,100 tests to 59 files and 1,061 tests, with every `app/` line and branch still covered as
  before (92.69%). Guardrail, refusal, exit-code and JSON-contract tests were only deduplicated, and
  `pytest.raises(..., match=)` stays wherever it shows which gate rejected a patch.
- `README_AR.md`, a partial Arabic translation whose language links pointed at files that do not
  exist. `README.ar.md` follows the naming of the other translations and stays.

### Security
- **One redaction boundary for every prompt and every log line** (#377). The Diagnoser, Patch
  Generator, Reviewer and decline verdict now build their prompts from the same prepared evidence,
  so the failure log, DOM diff, ARIA snapshot and model-reported analysis are redacted once instead
  of per call site; the failure line number is still taken from the raw log, so redaction cannot
  shift where a fix is applied. Built-in credential patterns are joined by two new settings:
  `E2E_HEALER_SENSITIVE_ENV_VARS` (variable **names**, resolved at redaction time so their values
  never enter the settings object) and `E2E_HEALER_SENSITIVE_VALUES` (literals, held as masked
  secrets). A mistyped name or a literal shorter than four characters fails at load rather than
  redacting nothing. Structured-log events pass through the same redaction before rendering,
  including exception text. Test source is deliberately **not** redacted — name a hard-coded
  credential in `SENSITIVE_VALUES` to keep it out of a prompt.

## [0.5.0] - 2026-09-22

### Added
- **Repair/refusal evidence bundle** — `--json` now emits schema `2.0` results with a
  deterministic, redacted evidence bundle: parsed failure and selector, DOM context, ARIA snapshot
  reference, candidate verification scores, and loop history. Terminal failures emit a first-class
  `RefusalReport` with its exact refusal reason; consumers must update from schema `1.0` before
  accepting these results (#296).
- **`--root` path anchor** — `e2e-healer heal` / `review` accept `--root <dir>` to anchor all
  relative-path resolution (test spec, diff file, test-results dir, and the `git diff` /
  Playwright subprocess cwd) to a project root. This lets programmatic integrators (e.g. a
  Playwright Reporter) invoke the CLI from an arbitrary working directory — the explicit
  equivalent of the shell wrapper's `cd $working-directory` (#301).
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
  overwrites an existing one (#126).
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

[Unreleased]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.5-pre...v0.5.0
[0.5.0-pre]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.4.0...v0.5-pre
[0.4.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.2.0...v0.2.2
[0.2.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Lee-Dongwook/E2E-Self-Heal/releases/tag/v0.1.0
