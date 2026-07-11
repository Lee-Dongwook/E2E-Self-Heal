# Example: healing a renamed selector

A **runnable** Playwright project that reproduces a broken test and shows the engine
repairing it. The served page ([`index.html`](index.html)) uses `id="submit"`, but the
test still targets the old `#submit-btn` — so it times out until healed.

## Files

| File                          | Role                                                           |
| ----------------------------- | -------------------------------------------------------------- |
| `index.html`                  | The page under test (button id already renamed to `submit`)    |
| `example.spec.ts`             | The failing test (still clicks `#submit-btn`)                  |
| `components/SubmitButton.tsx` | The source component the rename came from                      |
| `selector-rename.diff`        | The change that broke the test — fed to the healer as `--diff` |
| `playwright.config.ts`        | Serves this folder statically (Python) and runs the spec       |

## 1. See the failure

Requires Node and Python 3 (the static server). Run from this folder:

```bash
cd examples
npm install
npx playwright install chromium
npx playwright test 2>&1 | tee playwright.log
```

The test fails with a Timeout on `#submit-btn`, and the log is saved to `playwright.log`.

## 2. Heal it

With the healer installed (`uv sync` at the repo root) and `E2E_HEALER_NVIDIA_API_KEY`
set, run from this folder:

```bash
e2e-healer example.spec.ts --log playwright.log --diff selector-rename.diff --dry-run
```

The engine parses the Timeout, correlates it with the `id` change in the diff, and proposes:

```diff
-  await page.click("#submit-btn");
+  await page.click("#submit");
```

`--dry-run` restores the file afterward. Drop it to write the fix in place, then re-run
`npx playwright test` to confirm it passes (the assertion on `Thanks!` is left untouched).

> In a real repo you don't need `--diff` — the tool defaults to `git diff`, and omitting
> `--log` makes it run the test itself to capture the failure.

---

## Second scenario: renamed className

[`classname-scenario/`](classname-scenario/) mirrors the id-rename walkthrough above, but the
breakage is a **CSS class change** (`cta-button` → `cta-primary`) instead of an element id.
See [`classname-scenario/README.md`](classname-scenario/README.md) for reproduce + heal steps.
