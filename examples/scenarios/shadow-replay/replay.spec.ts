import { expect, test } from "@playwright/test";

test("replays storage state and network snapshots in the test context", async ({ page }) => {
  await page.goto("https://shadow.example.test/");

  await expect
    .poll(() => page.evaluate(() => localStorage.getItem("theme")))
    .toBe("dark");

  const apiBody = await page.evaluate(async () => {
    const response = await fetch("https://api.example.test/data");
    return response.text();
  });
  expect(apiBody).toBe("mocked_body");
});
