import { spawnSync } from "node:child_process";

const npmBin = process.platform === "win32" ? "npm.cmd" : "npm";

function run(command, args, options = {}) {
  process.stdout.write(`\n$ ${[command, ...args].join(" ")}\n`);
  const result = spawnSync(command, args, { stdio: "inherit", ...options });
  if (result.status !== 0) {
    throw new Error(`Command failed: ${[command, ...args].join(" ")}`);
  }
}

function main() {
  run(npmBin, ["run", "typecheck"]);
  run(npmBin, ["run", "build"]);
  run(npmBin, ["run", "openapi:check"]);
  run(npmBin, ["run", "tests"]);
  run(npmBin, ["run", "redaction:scan"]);
  run(npmBin, ["run", "audit:check"]);
  run(npmBin, ["run", "e2e"]);
  process.stdout.write("\nOK\n");
}

try {
  main();
} catch (err) {
  process.stderr.write(`${err?.stack ?? err}\n`);
  process.exitCode = 1;
}

