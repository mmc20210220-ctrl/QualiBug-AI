import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const npmBin = process.platform === "win32" ? "npm.cmd" : "npm";

function run(command, args, options = {}) {
  process.stdout.write(`\n$ ${[command, ...args].join(" ")}\n`);
  const result = spawnSync(command, args, { stdio: "inherit", shell: process.platform === "win32", ...options });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`Command failed: ${[command, ...args].join(" ")}`);
  }
}

function resolvePlaywrightBrowserDir() {
  const configured = process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (configured && configured !== "0") return configured;
  if (process.platform === "win32") {
    const localAppData = process.env.LOCALAPPDATA;
    if (localAppData) return path.join(localAppData, "ms-playwright");
  }
  return path.join(os.homedir(), ".cache", "ms-playwright");
}

function playwrightBrowsersPresent() {
  const base = resolvePlaywrightBrowserDir();
  if (!existsSync(base)) return false;
  const entries = readdirSync(base);
  for (const entry of entries) {
    if (!/^chromium/i.test(entry)) continue;
    const dir = path.join(base, entry);
    try {
      if (!statSync(dir).isDirectory()) continue;
    } catch {
      continue;
    }
    const candidates = [
      path.join(dir, "chrome-win", "chrome.exe"),
      path.join(dir, "chrome-linux", "chrome"),
      path.join(dir, "chrome-mac", "Chromium.app"),
      path.join(dir, "chrome-headless-shell-win64", "chrome-headless-shell.exe"),
      path.join(dir, "chrome-headless-shell-linux64", "chrome-headless-shell"),
      path.join(dir, "chrome-headless-shell-mac", "Chromium.app"),
    ];
    if (candidates.some((p) => existsSync(p))) return true;
  }
  return false;
}

function main() {
  run(npmBin, ["run", "typecheck"]);
  run(npmBin, ["run", "build"]);
  run(npmBin, ["run", "openapi:check"]);
  run(npmBin, ["run", "tests"]);
  run(npmBin, ["run", "redaction:scan"]);
  run(npmBin, ["run", "audit:check"]);
  if (playwrightBrowsersPresent()) {
    run(npmBin, ["run", "e2e"]);
  } else if (process.env.CI || process.env.QUALIBUG_E2E_REQUIRED === "1") {
    throw new Error("Playwright browsers missing. Run: npx playwright install");
  } else {
    process.stdout.write("\nSKIP e2e: Playwright browsers missing (run: npx playwright install)\n");
  }
  process.stdout.write("\nOK\n");
}

try {
  main();
} catch (err) {
  process.stderr.write(`${err?.stack ?? err}\n`);
  process.exitCode = 1;
}
