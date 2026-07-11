# Example: healing a renamed className

A second runnable scenario under `examples/` — the page ([`index.html`](index.html)) uses
`class="cta-primary"`, but the test still clicks the old `.cta-button` selector, so it
times out until healed.

## Files

| File                          | Role                                                              |
| ----------------------------- | ----------------------------------------------------------------- |
| `index.html`                  | The page under test (button class already renamed to `cta-primary`) |
| `classname.spec.ts`           | The failing test (still clicks `.cta-button`)                     |
| `components/CTAButton.tsx`    | The source component the rename came from                         |
| `classname-rename.diff`       | The change that broke the test — fed to the healer as `--diff`    |

## 1. See the failure

From the `examples/` folder (shared Playwright config):

```bash
cd examples
npm install
npx playwright install chromium
npx playwright test classname-scenario/classname.spec.ts 2>&1 | tee classname-scenario/playwright.log
```

The test fails with a Timeout on `.cta-button`.

## 2. Heal it

With the healer installed and `E2E_HEALER_NVIDIA_API_KEY` set:

```bash
cd examples/classname-scenario
e2e-healer classname.spec.ts --log playwright.log --diff classname-rename.diff --dry-run
```

The engine should propose:

```diff
-  await page.click(".cta-button");
+  await page.click(".cta-primary");
```

Drop `--dry-run` to write the fix in place, then re-run the spec to confirm it passes
(the assertion on `Welcome!` is left untouched).
