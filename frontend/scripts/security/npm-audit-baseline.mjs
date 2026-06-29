import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const ROOT = path.resolve(process.cwd());
const BASELINE_PATH = path.join(ROOT, "security", "npm-audit-baseline.json");
const npmBin = process.platform === "win32" ? "npm.cmd" : "npm";

const SEVERITY_RANK = {
  info: 0,
  low: 1,
  moderate: 2,
  high: 3,
  critical: 4,
};

function normalizeVia(via) {
  if (typeof via === "string") return via;
  if (!via || typeof via !== "object") return "unknown";
  const obj = via;
  const parts = [];
  if (obj.source !== undefined) parts.push(`source:${String(obj.source)}`);
  if (obj.name) parts.push(`name:${String(obj.name)}`);
  if (obj.title) parts.push(`title:${String(obj.title)}`);
  if (!parts.length) return "unknown";
  return parts.join("|");
}

function normalizeAuditReport(report, minimumSeverity) {
  const minimumRank = SEVERITY_RANK[minimumSeverity] ?? 0;
  const out = [];
  const vulnerabilities = report?.vulnerabilities && typeof report.vulnerabilities === "object" ? report.vulnerabilities : {};
  for (const [name, vuln] of Object.entries(vulnerabilities)) {
    if (!vuln || typeof vuln !== "object") continue;
    const severity = typeof vuln.severity === "string" ? vuln.severity : "unknown";
    const rank = SEVERITY_RANK[severity] ?? 0;
    if (rank < minimumRank) continue;
    const via = Array.isArray(vuln.via) ? vuln.via.map(normalizeVia).sort() : [];
    out.push({
      name,
      severity,
      range: typeof vuln.range === "string" ? vuln.range : "",
      via,
    });
  }
  out.sort((a, b) => (a.name === b.name ? a.severity.localeCompare(b.severity) : a.name.localeCompare(b.name)));
  return out;
}

function runNpmAuditJson() {
  let result = spawnSync(npmBin, ["audit", "--json"], { cwd: ROOT, encoding: "utf8" });
  if (result.error && process.env.npm_execpath) {
    result = spawnSync(process.execPath, [process.env.npm_execpath, "audit", "--json"], { cwd: ROOT, encoding: "utf8" });
  }
  if (result.error) {
    throw result.error;
  }
  const text = (result.stdout || "").trim() || (result.stderr || "").trim();
  if (!text) {
    throw new Error("npm audit --json produced no output");
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`Failed to parse npm audit JSON output:\n${text.slice(0, 2000)}`);
  }
}

function readBaseline() {
  if (!fs.existsSync(BASELINE_PATH)) {
    return {
      schemaVersion: 1,
      minimumSeverity: "low",
      allow: [],
    };
  }
  const baseline = JSON.parse(fs.readFileSync(BASELINE_PATH, "utf8"));
  const minimumSeverity = typeof baseline.minimumSeverity === "string" ? baseline.minimumSeverity : "low";
  const allow = Array.isArray(baseline.allow) ? baseline.allow : [];
  return { schemaVersion: 1, minimumSeverity, allow };
}

function writeBaseline(baseline) {
  fs.mkdirSync(path.dirname(BASELINE_PATH), { recursive: true });
  fs.writeFileSync(BASELINE_PATH, JSON.stringify(baseline, null, 2) + "\n");
}

function stableKey(v) {
  return JSON.stringify(v);
}

function main() {
  const baseline = readBaseline();
  const report = runNpmAuditJson();
  const current = normalizeAuditReport(report, baseline.minimumSeverity);

  if (process.env.UPDATE_AUDIT_BASELINE === "1") {
    writeBaseline({ schemaVersion: 1, minimumSeverity: baseline.minimumSeverity, allow: current });
    process.stdout.write(`Updated baseline: ${path.relative(ROOT, BASELINE_PATH)}\n`);
    return;
  }

  const allowSet = new Set(baseline.allow.map(stableKey));
  const currentSet = new Set(current.map(stableKey));
  const newFindings = current.filter((v) => !allowSet.has(stableKey(v)));

  if (newFindings.length) {
    const lines = newFindings
      .map((v) => `- ${v.name} (${v.severity}) ${v.range ? `[${v.range}] ` : ""}${v.via.join(", ")}`)
      .join("\n");
    throw new Error(
      [
        "npm audit baseline check failed: detected new vulnerabilities.",
        `Baseline: ${path.relative(ROOT, BASELINE_PATH)}`,
        `Update:   UPDATE_AUDIT_BASELINE=1 ${npmBin} run audit:check`,
        "",
        lines,
      ].join("\n"),
    );
  }

  const staleFindings = baseline.allow.filter((v) => !currentSet.has(stableKey(v)));
  if (staleFindings.length) {
    process.stdout.write(
      `Stale baseline entries detected (${staleFindings.length}). Consider refreshing baseline with UPDATE_AUDIT_BASELINE=1.\n`,
    );
  }

  process.stdout.write("OK\n");
}

try {
  main();
} catch (err) {
  process.stderr.write(`${err?.stack ?? err}\n`);
  process.exitCode = 1;
}
