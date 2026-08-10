import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const card = read('src/components/findings/FindingCard.tsx');
const types = read('src/types/index.ts');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(card.includes('复制研发交接'), 'finding card must expose developer handoff');
assert(card.includes('navigator.clipboard.writeText(handoffSummary(finding))'), 'developer handoff must use real finding data');
assert(card.includes('finding.regression.lifecycle_label'), 'finding collaboration must surface real regression lifecycle');
assert(card.includes('finding.regression.latest_status_label'), 'finding collaboration must surface real latest regression status');
assert(card.includes('finding.regression.gate_status'), 'finding collaboration must surface real regression gate');
assert(card.includes('当前前端不使用浏览器本地状态伪装这些企业协作字段'), 'unsupported collaboration fields must be explicitly honest');
assert(!card.includes('localStorage.setItem'), 'finding collaboration must not persist fake workflow state in localStorage');
assert(!card.includes('sessionStorage.setItem'), 'finding collaboration must not persist fake workflow state in sessionStorage');
assert(types.includes('regression?: {'), 'finding schema must remain the source for regression facts');
assert(packageJson.includes('"test:finding-collaboration": "node scripts/finding-collaboration-contract.mjs"'), 'package script missing finding collaboration contract');
assert(ciGate.includes('"test:finding-collaboration"'), 'ci gate missing finding collaboration contract');

console.log('finding collaboration contract passed');
