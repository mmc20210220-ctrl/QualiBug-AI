import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const cards = read('src/components/dashboard/DecisionCards.tsx');
const diagnostics = read('src/components/TechnicalDiagnostics.tsx');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

for (const label of ['管理视图', '测试视图', '技术视图']) {
  assert(cards.includes(label), `dashboard role view missing: ${label}`);
}
assert(cards.includes('视图只改变信息优先级，不改变底层结果、风险口径或证据'), 'role views must preserve one source of truth');
assert(cards.includes('cards.filter(isQualityCard)'), 'quality view must reuse existing decision cards');
assert(cards.includes("cards.filter((card) => !isQualityCard(card))"), 'management view must reuse existing decision cards');
assert(cards.includes('OPEN_TECHNICAL_DIAGNOSTICS_EVENT'), 'technical view must open existing diagnostics instead of duplicating data');
assert(diagnostics.includes('id="dashboard-technical-diagnostics"'), 'technical diagnostics anchor missing');
assert(diagnostics.includes('window.addEventListener(OPEN_TECHNICAL_DIAGNOSTICS_EVENT'), 'technical diagnostics must respond to role view navigation');
assert(packageJson.includes('"test:dashboard-role-views": "node scripts/dashboard-role-views-contract.mjs"'), 'package script missing dashboard role views contract');
assert(ciGate.includes('"test:dashboard-role-views"'), 'ci gate missing dashboard role views contract');

console.log('dashboard role views contract passed');
