import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const evidencePackage = read('src/lib/finding-evidence-package.ts');
const drawer = read('src/components/findings/EvidenceDrawer.tsx');
const distributionTools = read('src/components/evidence/EvidenceDistributionTools.tsx');
const responsive = read('src/styles/customer-responsive.css');
const report = read('src/api/report.ts');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

for (const secretPattern of ['authorization', 'cookie', 'set-cookie', 'token', 'api[_-]?key', 'password']) {
  assert(evidencePackage.toLowerCase().includes(secretPattern), `external evidence redaction missing pattern: ${secretPattern}`);
}
assert(evidencePackage.includes(".replace(/&/g, '&amp;')"), 'printable evidence must HTML-escape ampersands');
assert(evidencePackage.includes(".replace(/</g, '&lt;')"), 'printable evidence must HTML-escape angle brackets');
assert(evidencePackage.includes('打印 / 保存为 PDF'), 'printable evidence package must support browser PDF export');
assert(evidencePackage.includes('该页面不会生成公开链接，也不会自动上传到第三方服务'), 'local printable package must not imply server-side sharing');

assert(drawer.includes('<FindingDecisionSnapshot finding={finding} compact />'), 'drawer must lead with the shared finding decision snapshot');
assert(drawer.includes('<EvidenceDistributionTools finding={finding} project={project} />'), 'drawer must delegate export/share behavior to secondary evidence tools');
assert(drawer.indexOf('<FindingDecisionSnapshot finding={finding} compact />') < drawer.indexOf('<EvidenceDistributionTools finding={finding} project={project} />'), 'distribution tools must appear after customer decision context');
assert(drawer.includes('先核对问题为什么成立'), 'drawer must prioritize evidence review before distribution');

assert(distributionTools.includes('evidence-distribution-tools'), 'evidence tools need a dedicated responsive scope');
assert(distributionTools.includes('buildFindingEvidencePackageText(finding)'), 'copy action must use redacted evidence builder');
assert(distributionTools.includes('buildFindingEvidencePackageHtml(finding)'), 'print action must use redacted evidence builder');
assert(distributionTools.includes('复制/打印/只读分享都会重新执行服务端或前端脱敏，不直接外发该原始文本'), 'raw curl must remain separated from every external distribution path');
assert(distributionTools.includes('公开链接只能读取创建当刻冻结的脱敏快照'), 'readonly sharing must remain distinct from local copies');
assert(distributionTools.includes('这些是证据核对后的分发工具，不参与问题是否成立或是否修复的判断'), 'distribution must remain secondary to validation truth');
assert(!evidencePackage.includes('finding.reproduction.curl_command'), 'local external evidence package must not directly include raw curl commands');

assert(responsive.includes('.evidence-drawer-head .settings-actions'), 'drawer header actions must wrap responsively');
assert(responsive.includes('.evidence-distribution-tools .settings-actions'), 'distribution actions must wrap responsively');
assert(responsive.includes('@media (max-width: 560px)'), 'evidence distribution needs a narrow-screen breakpoint');
assert(responsive.includes('.evidence-distribution-tools .settings-actions .btn'), 'distribution buttons must become touch-friendly on narrow screens');

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
