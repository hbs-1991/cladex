import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  // Traces, screenshots and the HTML report live under node_modules so a failed
  // run leaves nothing for `git add web` to pick up.
  outputDir: "node_modules/.playwright/results",
  reporter: process.env.CI
    ? [["list"], ["html", { outputFolder: "node_modules/.playwright/report", open: "never" }]]
    : [["list"]],
  use: { baseURL: "http://localhost:4173" },
  webServer: {
    command: "npm run build && npm run preview -- --port 4173",
    port: 4173,
    reuseExistingServer: !process.env.CI,
  },
});
