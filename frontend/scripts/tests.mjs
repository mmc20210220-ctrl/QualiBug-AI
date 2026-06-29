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

const BEHAVIOR_SPACE_FILES = {
  page: path.join(SRC, "app", "(app)", "projects", "[projectId]", "behavior-space", "page.tsx"),
  deferred: path.join(SRC, "components", "behavior-space", "BehaviorSpaceDeferredVisualizations.tsx"),
  sandbox: path.join(SRC, "components", "behavior-space", "BehaviorSpaceSandbox.tsx"),
  flow: path.join(SRC, "components", "behavior-space", "BehaviorSpaceFlow.tsx"),
};

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

function assertSourceIncludes(filePath, requiredSnippets) {
  const text = fs.readFileSync(filePath, "utf8");
  const missing = requiredSnippets.filter((snippet) => !text.includes(snippet));
  if (missing.length) {
    throw new Error(`Behavior Space guard failed: ${path.relative(ROOT, filePath)} missing ${missing.join(", ")}`);
  }
  return text;
}

function scanBehaviorSpaceGuards() {
  const pageText = assertSourceIncludes(BEHAVIOR_SPACE_FILES.page, [
    "是否可上线",
    "风险成本",
    "下一步动作",
    "页面优先回答上线建议、风险成本和下一步动作",
    "behavior-space-replay",
    "behavior-space-audit",
  ]);
  const deferredText = assertSourceIncludes(BEHAVIOR_SPACE_FILES.deferred, [
    "behavior-space-2d",
    'import("./BehaviorSpaceFlow")',
    'import("./BehaviorSpaceSandbox")',
  ]);
  const sandboxText = assertSourceIncludes(BEHAVIOR_SPACE_FILES.sandbox, [
    "只用于高价值演示，不替代 2D 主工作流",
    "打开 2.5D 演示层",
    "继续用 2D 主视图分析",
  ]);
  const flowText = assertSourceIncludes(BEHAVIOR_SPACE_FILES.flow, ["2D 分层主视图"]);

  const pageForbidden = [/riskId\s*\{/, /pathId\s*\{/, /\{audit\.kind\}/];
  const sandboxForbidden = [/\{\s*selected\?\.node\.kind\s*\}/];
  const flowForbidden = [/\{evidence\.kind\}/];
  if (!flowText.includes("2D 主视图") && !flowText.includes("2D 分层主视图")) {
    throw new Error("Behavior Space guard failed: missing 2D primary-view copy");
  }
  const violations = [
    ...pageForbidden.filter((rule) => rule.test(pageText)).map((rule) => `page:${rule}`),
    ...sandboxForbidden.filter((rule) => rule.test(sandboxText)).map((rule) => `sandbox:${rule}`),
    ...flowForbidden.filter((rule) => rule.test(flowText)).map((rule) => `flow:${rule}`),
  ];
  if (violations.length) {
    throw new Error(`Behavior Space guard failed: raw technical fields leaked into UI source\n- ${violations.join("\n- ")}`);
  }
}

function main() {
  for (const filePath of REQUIRED_FILES) assertFileExists(filePath);
  scanForbiddenTokens();
  scanBehaviorSpaceGuards();
  process.stdout.write("OK\n");
}

main();
