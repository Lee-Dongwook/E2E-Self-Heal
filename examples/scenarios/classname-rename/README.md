# Example: healing a renamed className

This scenario intentionally has a broken `.cta-button` selector. The React demo app now
renders the renamed `cta-primary` class, so the scenario is collected by the shared
Playwright configuration and reproduces a real failure.

## Files

| File | Role |
| --- | --- |
| `spec.ts` | The failing test, which still clicks `.cta-button` |
| `change.patch` | The class rename diff to pass to the healer |
| `../../demo-app/src/components/CTAButton.tsx` | The source component with `cta-primary` |

## 1. See the failure

From the `examples/` folder:

```bash
pnpm install
pnpm exec playwright install chromium
pnpm exec playwright test scenarios/classname-rename 2>&1 | tee classname-rename-playwright.log
```

The test fails with a timeout on `.cta-button`.

## 2. Heal it

With the healer installed and `E2E_HEALER_NVIDIA_API_KEY` set:

```bash
e2e-healer scenarios/classname-rename/spec.ts \
  --log classname-rename-playwright.log \
  --diff scenarios/classname-rename/change.patch \
  --dry-run
```

The engine should propose replacing `.cta-button` with `.cta-primary`. Drop `--dry-run`
to write the fix, then rerun the same Playwright command; `Welcome!` should be visible.
