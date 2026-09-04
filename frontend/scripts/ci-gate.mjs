import { spawn } from "node:child_process";
import process from "node:process";

const npmCli = process.env.npm_execpath;
if (!npmCli) {
  throw new Error("npm_execpath is required; run this gate through npm run ci:gate");
}

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: "inherit" });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`${command} ${args.join(" ")} failed: code=${code} signal=${signal || "none"}`));
    });
  });
}

async function waitForFrontend(url, server) {
  const deadline = Date.now() + 30_000;
  let lastError = "frontend did not respond";
  while (Date.now() < deadline) {
    if (server.exitCode !== null) {
      throw new Error(`Vite exited before readiness: code=${server.exitCode}`);
    }
    try {
      const response = await fetch(url);
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Vite readiness timeout at ${url}: ${lastError}`);
}

for (const script of [
  "typecheck",
  "lint",
  "test",
  "brand:check",
  "test:brand-mark",
  "test:autonomous-ux",
  "test:agent-workspace",
  "test:frontend-finalization",
  "test:requirement-intelligence",
  "test:test-intelligence",
  "test:settings-onboarding",
  "test:run-lifecycle",
  "test:run-customer-result",
  "test:run-preflight-decision",
  "test:live-scan-status",
  "test:dashboard-role-views",
  "test:dashboard-decision-first",
  "test:release-decision-first",
  "test:customer-action-guidance",
  "test:finding-context-navigation",
  "test:finding-validation-boundary",
  "test:finding-verification-timeline",
  "test:finding-verification-focus",
  "test:finding-decision-snapshot",
  "test:evidence-distribution",
  "test:evidence-share",
  "test:materials-coverage",
  "test:materials-acceptance",
  "test:materials-remote-lifecycle",
]) {
  await run(process.execPath, [npmCli, "run", script]);
}

const vite = spawn(
  process.execPath,
  ["node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "5174", "--strictPort"],
  { stdio: "inherit" },
);

try {
  await waitForFrontend("http://127.0.0.1:5174/login", vite);
  await run(process.execPath, [npmCli, "run", "test:login-contract"]);
  await run(process.execPath, [npmCli, "run", "test:login-radar"]);
} finally {
  if (vite.exitCode === null) {
    vite.kill();
    await new Promise((resolve) => vite.once("exit", resolve));
  }
}

await run(process.execPath, [npmCli, "run", "build"]);
