import { defineConfig } from "@playwright/test";

// Runs the scenario specs against the real React app. The webServer builds the Vite
// app and serves it via `vite preview`, so a change applied to a component (e.g.
// `git apply scenarios/id-rename/change.patch`) is reflected on the next test run.
export default defineConfig({
  testDir: "./scenarios",
  testMatch: ["**/spec.ts", "**/*.spec.ts"],
  timeout: 30_000,
  expect: { timeout: 3_000 },
  use: {
    baseURL: "http://localhost:4173",
    actionTimeout: 3_000,
  },
  webServer: {
    command: "pnpm build && pnpm preview",
    url: "http://localhost:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  reporter: [
    ["list"],
    [
      "../integrations/playwright-reporter/src/index.ts",
      { diffFile: "scenarios/classname-rename/change.patch" },
    ],
  ],
});
