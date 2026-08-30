import { defineConfig } from "@playwright/test";

export default defineConfig({
  workers: 1,
  reporter: "line",
  use: {
    headless: true,
  },
});
