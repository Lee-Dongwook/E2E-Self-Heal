import { test, expect } from "@playwright/test";

// This test breaks after CTAButton's className is renamed from `cta-button` to `cta-primary`.
// The healer should patch the selector below while leaving the assertion untouched.
test("clicks the CTA", async ({ page }) => {
  await page.goto("/classname-scenario/");
  await page.click(".cta-button");
  await expect(page.getByText("Welcome!")).toBeVisible();
});
