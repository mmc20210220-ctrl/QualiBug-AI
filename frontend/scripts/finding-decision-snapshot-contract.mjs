import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const presentation = read('src/lib/finding-decision-presentation.ts');
const snapshot = read('src/components/findings/FindingDecisionSnapshot.tsx');
const card = read('src/components/findings/FindingCard.tsx');
const evidence = read('src/pages/EvidenceChain.tsx');
const styles = read('src/components/findings/FindingDecisionSnapshot.css');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(presentation.includes("deriveFindingVerification(finding)"), 'decision snapshot must reuse shared verification truth');
assert(presentation.includes("finding.business_summary"), 'decision snapshot must prefer backend business summary for impact');
assert(presentation.includes("finding.expected_actual_comparison?.difference"), 'decision basis must preserve backend expected/actual difference when available');
assert(presentation.includes("finding.evidence_quality?.label"), 'decision evidence status must consume backend evidence-quality label');
assert(presentation.includes("finding.evidence_chain?.length || 0"), 'decision evidence status must report the real evidence-chain count');
assert(presentation.includes("finding.proof?.repro_rate"), 'decision snapshot must preserve reported reproduction truth');
assert(presentation.includes("verification.nextActionLabel"), 'decision snapshot next action must come from shared verification presentation');
assert(presentation.includes("verification.detail"), 'decision snapshot next-action detail must stay tied to shared verification truth');
assert(!presentation.includes('score >='), 'frontend must not invent evidence sufficiency thresholds');
assert(!presentation.includes('confidence >='), 'frontend must not invent finding-confidence thresholds');
assert(!presentation.includes("= '已修复'"), 'frontend presentation must not independently assign a fixed verdict');

for (const label of ['发生了什么', '为什么成立', '证据状态', '当前验证结论', '下一步验证']) {
  assert(snapshot.includes(label), `decision snapshot missing first-screen question: ${label}`);
}
assert(snapshot.includes('<FindingVerificationStatus finding={finding} compact />'), 'decision snapshot must expose current shared verification status');
assert(snapshot.includes('不会根据前端展示自行判定“已修复”'), 'decision snapshot must state that presentation cannot invent fix status');
assert(snapshot.includes('替代项目级 Release Gate'), 'decision snapshot must preserve project release authority');

assert(card.includes('<FindingDecisionSnapshot finding={finding} />'), 'expanded Finding must show the decision snapshot before detailed verification');
assert(card.indexOf('<FindingDecisionSnapshot finding={finding} />') < card.indexOf('<FindingVerificationPanel'), 'Finding decision snapshot must precede the full verification panel');
assert(card.includes('查看预期 / 实际与复现细节'), 'detailed reproduction facts must remain available after the first-screen summary');

assert(evidence.includes('<FindingDecisionSnapshot finding={selected} compact />'), 'Evidence Center must reuse the same decision snapshot for the selected Finding');
assert(evidence.indexOf('<FindingDecisionSnapshot finding={selected} compact />') < evidence.indexOf('<QualityScore finding={selected} />'), 'Evidence Center must show customer decision context before detailed evidence scoring');
assert(evidence.indexOf('<FindingDecisionSnapshot finding={selected} compact />') < evidence.indexOf('<FindingVerificationPanel'), 'Evidence Center must show the concise decision context before the full verification history');

assert(styles.includes('grid-template-columns: repeat(2, minmax(0, 1fr));'), 'decision snapshot must have a compact desktop grid');
assert(styles.includes('@media (max-width: 640px)'), 'decision snapshot must remain readable on mobile');
assert(styles.includes('grid-template-columns: minmax(0, 1fr);'), 'decision snapshot must collapse to one column on narrow screens');

assert(packageJson.includes('"test:finding-decision-snapshot": "node scripts/finding-decision-snapshot-contract.mjs"'), 'package script missing finding decision snapshot contract');
assert(ciGate.includes('"test:finding-decision-snapshot"'), 'ci gate missing finding decision snapshot contract');

console.log('finding decision snapshot contract passed');
