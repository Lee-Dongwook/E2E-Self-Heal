import { expect, test } from "@playwright/test";

// GREEN on checkout: submitting the form calls handleSubmit and shows `Thanks!`.
//
// Apply scenarios/product-regression/change.patch to remove that handler. The submit
// button still exists and is clickable, but the expected product behavior is gone.
// This is a product regression: a repair must preserve the assertion and refuse to
// rewrite the test to make it pass.
test("submits the form", async ({ page }) => {
  await page.goto("/");
  await page.click("#submit-btn");
  await expect(page.getByText("Thanks!")).toBeVisible();
});
