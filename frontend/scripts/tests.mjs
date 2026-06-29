import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(process.cwd());
const SRC = path.join(ROOT, "src");

const REQUIRED_FILES = [
  path.join(SRC, "proxy.ts"),
  path.join(SRC, "app", "auth", "login", "route.ts"),
  path.join(SRC, "app", "auth", "callback", "route.ts"),
  path.join(SRC, "app", "auth", "logout", "route.ts"),
  path.join(SRC, "app", "(auth)", "no-access", "page.tsx"),
];

const FORBIDDEN_TOKENS = [
  "X-QualiBug-Actor",
  "X-QualiBug-Role",
  "X-QualiBug-Project-Scopes",
  "\"X-QualiBug-Actor\":\"admin\"",
  "\"X-QualiBug-Role\":\"admin\"",
  "X-QualiBug-Actor':'admin",
  "X-QualiBug-Role':'admin",
];

function* walk(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else yield full;
  }
}

function assertFileExists(filePath) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`Missing required file: ${path.relative(ROOT, filePath)}`);
  }
}

function scanForbiddenTokens() {
  const violations = [];
  for (const filePath of walk(SRC)) {
    if (!/\.(ts|tsx|mts|js|jsx)$/.test(filePath)) continue;
    const text = fs.readFileSync(filePath, "utf8");
    const hits = FORBIDDEN_TOKENS.filter((token) => text.includes(token));
    if (hits.length) violations.push({ filePath, hits });
  }
  if (violations.length) {
    const lines = violations
      .map((v) => `- ${path.relative(ROOT, v.filePath)}: ${v.hits.join(", ")}`)
      .join("\n");
    throw new Error(`Forbidden trusted identity header tokens found:\n${lines}`);
  }
}

function main() {
  for (const filePath of REQUIRED_FILES) assertFileExists(filePath);
  scanForbiddenTokens();
  process.stdout.write("OK\n");
}

main();
