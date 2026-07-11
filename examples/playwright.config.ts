import { defineConfig } from "@playwright/test";

// Minimal config: serve this folder as a static site and run the spec against it.
// A short timeout keeps the "broken selector" failure fast to reproduce.
export default defineConfig({
  testDir: ".",
  testMatch: "**/*.spec.ts",
  timeout: 15_000,
  expect: { timeout: 3_000 },
  use: {
    baseURL: "http://localhost:4173",
    actionTimeout: 3_000,
  },
  webServer: {
    command: "python3 -m http.server 4173",
    url: "http://localhost:4173",
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
