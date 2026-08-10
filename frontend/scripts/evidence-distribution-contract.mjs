import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const evidencePackage = read('src/lib/finding-evidence-package.ts');
const drawer = read('src/components/findings/EvidenceDrawer.tsx');
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
assert(packageJson.includes('"test:evidence-distribution": "node scripts/evidence-distribution-contract.mjs"'), 'package script missing evidence distribution contract');
assert(ciGate.includes('"test:evidence-distribution"'), 'ci gate missing evidence distribution contract');

console.log('evidence distribution contract passed');
