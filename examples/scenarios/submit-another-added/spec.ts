import { expect, test } from "@playwright/test";

test("submits the form", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Submit" }).click();
  await expect(page.getByText("Thanks!")).toBeVisible();
});
