import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(process.cwd());
const TARGET_DIRS = [path.join(ROOT, "src", "app"), path.join(ROOT, "src", "components")];
const EXT_RE = /\.(ts|tsx|js|jsx|mjs|mts)$/;

const FORBIDDEN = [
  { re: /\{\s*JSON\.stringify\s*\(/, reason: "UI 层禁止直接渲染 JSON dump（可能导致敏感信息/traceback 泄露）" },
  { re: /<pre\b/i, reason: "UI 层禁止使用 <pre> 展示原始输出（容易退化为技术数据/JSON dump）" },
  { re: /dangerouslySetInnerHTML/, reason: "禁止使用 dangerouslySetInnerHTML（容易绕过脱敏策略）" },
  { re: /process\.env\./, reason: "UI 层禁止直接读取环境变量（可能渲染 secret）" },
  { re: /\b(error|err)\.stack\b/, reason: "UI 层禁止渲染 error.stack（可能泄露 traceback）" },
];

function* walk(dir) {
  if (!fs.existsSync(dir)) return;
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else yield full;
  }
}

function scanFile(filePath) {
  if (!EXT_RE.test(filePath)) return [];
  if (filePath.includes(`${path.sep}src${path.sep}app${path.sep}api${path.sep}`)) return [];
  const text = fs.readFileSync(filePath, "utf8");
  const hits = [];
  for (const rule of FORBIDDEN) {
    if (rule.re.test(text)) hits.push(rule.reason);
  }
  return hits;
}

function main() {
  const violations = [];
  for (const dir of TARGET_DIRS) {
    for (const filePath of walk(dir)) {
      const hits = scanFile(filePath);
      if (!hits.length) continue;
      violations.push({ filePath, hits });
    }
  }

  if (violations.length) {
    const lines = violations
      .map((v) => `- ${path.relative(ROOT, v.filePath)}: ${Array.from(new Set(v.hits)).join(" / ")}`)
      .join("\n");
    throw new Error(`Redaction scan failed:\n${lines}`);
  }

  process.stdout.write("OK\n");
}

main();
