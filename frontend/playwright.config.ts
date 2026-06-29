import { defineConfig } from "@playwright/test";

const npmBin = process.platform === "win32" ? "npm.cmd" : "npm";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  webServer: {
    command: process.env.CI ? `${npmBin} run start -- -p 3000` : `${npmBin} run dev -- -p 3000`,
    url: "http://127.0.0.1:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      use:
        process.platform === "win32"
          ? {
              browserName: "chromium",
              launchOptions: {
                executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
              },
            }
          : { browserName: "chromium" },
    },
  ],
});
