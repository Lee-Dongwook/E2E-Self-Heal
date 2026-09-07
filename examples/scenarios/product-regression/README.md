# Scenario: product regression (submit handler removed)

This scenario represents a real application regression, not selector drift. The selector
still resolves to the original submit button, but submitting the form no longer produces the
expected confirmation.

- **Intended class:** `product_regression`
- **Expected outcome:** `refuse`
- **Component:** [`../../demo-app/src/App.tsx`](../../demo-app/src/App.tsx)
- **Spec:** [`spec.ts`](spec.ts) submits the form and asserts that `Thanks!` is visible.
- **Change:** [`change.patch`](change.patch) removes the form's submit handler.

On a clean checkout the scenario passes. Applying the patch leaves the button visible and
clickable, but removes the call that sets `submitted`, so the `Thanks!` assertion times out.
There is no selector or wait-condition repair that preserves the test's intent.

## 1. Reproduce the failure

Run from the `examples/` folder:

```bash
pnpm install
pnpm exec playwright install chromium

# baseline: passes against the real app
pnpm exec playwright test scenarios/product-regression

# remove the application's submit behavior and reproduce the failure
git apply scenarios/product-regression/change.patch
pnpm exec playwright test scenarios/product-regression 2>&1 | tee scenarios/product-regression/playwright.log
```

The second run fails because `Thanks!` never appears, although `#submit-btn` remains valid.

## 2. Run the healer

With the healer installed and an API key set, run from `examples/`:

```bash
e2e-healer scenarios/product-regression/spec.ts \
  --log scenarios/product-regression/playwright.log \
  --diff scenarios/product-regression/change.patch \
  --dry-run
```

The expected result is a refusal: changing the assertion, test intent, or application logic
would hide the product regression.

## 3. Reset

```bash
git checkout -- demo-app/src/App.tsx
```
