import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const evidencePackage = read('src/lib/finding-evidence-package.ts');
const drawer = read('src/components/findings/EvidenceDrawer.tsx');
const report = read('src/api/report.ts');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

for (const secretPattern of ['authorization', 'cookie', 'set-cookie', 'token', 'api[_-]?key', 'password']) {
  assert(evidencePackage.toLowerCase().includes(secretPattern), `external evidence redaction missing pattern: ${secretPattern}`);
}
assert(evidencePackage.includes(".replace(/&/g, '&amp;')"), 'printable evidence must HTML-escape ampersands');
assert(evidencePackage.includes(".replace(/</g, '&lt;')"), 'printable evidence must HTML-escape angle brackets');
assert(evidencePackage.includes('打印 / 保存为 PDF'), 'printable evidence package must support browser PDF export');
assert(evidencePackage.includes('该页面不会生成公开链接，也不会自动上传到第三方服务'), 'evidence package must not imply server-side sharing');
assert(drawer.includes('buildFindingEvidencePackageText(finding)'), 'drawer copy action must use redacted evidence builder');
assert(drawer.includes('buildFindingEvidencePackageHtml(finding)'), 'drawer print action must use redacted evidence builder');
assert(drawer.includes('复制/打印外发包会重新执行脱敏，不直接复用该原始文本'), 'raw curl must be kept separate from external package');
assert(!evidencePackage.includes('finding.reproduction.curl_command'), 'external evidence package must not directly include raw curl commands');

assert(report.includes("import { escapeEvidenceHtml } from '../lib/finding-evidence-package';"), 'aggregate report must reuse external redaction and escaping');
assert(report.includes('<title>QualiBug AI 风险评级报告 — ${h(d.projectName)}</title>'), 'aggregate report title must escape project name');
assert(report.includes('${h(f.title)}'), 'aggregate report must escape finding titles');
assert(report.includes('${h(f.expected.slice(0,150))}'), 'aggregate report must escape expected behavior');
assert(report.includes('${h(f.actual.slice(0,150))}'), 'aggregate report must escape actual behavior');
assert(report.includes('${h(f.desc)}'), 'aggregate report must escape DB finding text');
assert(report.includes('${h(c.name)}'), 'aggregate report must escape release check names');
assert(report.includes('${h(c.detail)}'), 'aggregate report must escape release check detail');
assert(report.includes('打印 / 保存为 PDF'), 'aggregate report must support browser PDF export');

assert(packageJson.includes('"test:evidence-distribution": "node scripts/evidence-distribution-contract.mjs"'), 'package script missing evidence distribution contract');
assert(ciGate.includes('"test:evidence-distribution"'), 'ci gate missing evidence distribution contract');

console.log('evidence distribution contract passed');
