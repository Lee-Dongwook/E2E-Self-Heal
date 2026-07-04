import { test, expect } from "@playwright/test";

// This test breaks after SubmitButton's id is renamed from `submit-btn` to `submit`.
// The healer should patch the selector below while leaving the assertion untouched.
test("submits the form", async ({ page }) => {
  await page.goto("/");
  await page.click("#submit-btn");
  await expect(page.getByText("Thanks!")).toBeVisible();
});
