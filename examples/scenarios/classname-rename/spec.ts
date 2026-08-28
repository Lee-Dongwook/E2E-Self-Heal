import { expect, test } from "@playwright/test";

// This test intentionally uses the old className. The healer should update only this
// selector to `.cta-primary`; the `Welcome!` assertion describes the test's intent.
test("clicks the CTA", async ({ page }) => {
  await page.goto("/");
  await page.click(".cta-button");
  await expect(page.getByText("Welcome!")).toBeVisible();
});
