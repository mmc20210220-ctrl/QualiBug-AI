import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const verification = read('src/lib/finding-verification.ts');
const timeline = read('src/components/findings/FindingVerificationTimeline.tsx');
const verificationPanel = read('src/components/findings/FindingVerificationPanel.tsx');
const evidence = read('src/pages/EvidenceChain.tsx');
const release = read('src/pages/ReleaseGate.tsx');
const styles = read('src/styles/finding-verification.css');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(verification.includes('export function deriveVerificationRunPresentation('), 'history runs must reuse one shared status interpreter');
assert(verification.includes("outcome: 'fixed'"), 'passed history must map to a fixed outcome');
assert(verification.includes("outcome: 'open'"), 'failed history must map to an open outcome');
assert(verification.includes("outcome: 'unknown'"), 'inconclusive history must remain unknown');
assert(verification.includes('export function buildFindingVerificationTimeline(finding: Finding)'), 'verification timeline builder missing');
assert(verification.includes("kind: 'baseline'"), 'timeline must start from the original confirmed finding baseline');
assert(verification.includes("outcome: 'open'"), 'original confirmed finding must start as an open issue conclusion');
assert(verification.includes(".sort((left, right) => String(left.generated_at || '').localeCompare(String(right.generated_at || '')))"), 'timeline must present real history in chronological order');
assert(verification.includes("const isKnownOutcome = presentation.outcome === 'fixed' || presentation.outcome === 'open';"), 'only terminal fixed/open outcomes may change the issue conclusion');
assert(verification.includes('const changedConclusion = isKnownOutcome && presentation.outcome !== lastKnownOutcome;'), 'conclusion changes must require a real terminal transition');
assert(verification.includes("presentation.outcome === 'unknown'\n        ? '本轮未形成可确认结论'"), 'inconclusive runs must not be presented as conclusion changes');
assert(verification.includes('if (isKnownOutcome) lastKnownOutcome = presentation.outcome;'), 'unknown runs must not overwrite the last known issue conclusion');
assert(verification.includes('export function latestFindingConclusionChange'), 'frontend must expose the latest real conclusion-changing run');

assert(timeline.includes('真实验证历史'), 'timeline must identify itself as real validation history');
assert(timeline.includes('最近结论变化：${latestChange.transitionLabel}'), 'timeline must surface the latest real conclusion transition');
assert(timeline.includes('结论变化'), 'timeline must visibly mark the run that changed the issue conclusion');
assert(timeline.includes('Probe {event.run.regression_probe_id}'), 'timeline must retain the exact regression probe identity');
assert(timeline.includes('{event.run.method} {event.run.path}'), 'timeline must retain the real validation target');
assert(timeline.includes('后端尚未返回真实修复后验证历史。前端不会补造验证轮次'), 'missing history must remain explicit instead of synthetic');
assert(timeline.includes('const hasCollapsedHistory = compact && timeline.length > 4;'), 'compact history must have an explicit folding rule');
assert(timeline.includes('? [timeline[0], ...timeline.slice(-3)]'), 'compact release history must preserve the original finding baseline and latest runs');
assert(timeline.includes('中间 {collapsedCount} 次已折叠'), 'collapsed history must be explicit to the customer');

assert(verificationPanel.includes('<FindingVerificationTimeline finding={finding} />'), 'finding/evidence detailed validation must show the complete real timeline');
assert(evidence.includes('<FindingVerificationPanel finding={selected} />'), 'evidence center must inherit the complete timeline for the exact finding');
assert(release.includes('<FindingVerificationTimeline finding={requestedFinding} compact />'), 'release finding context must show a compact real conclusion timeline');
assert(release.includes('单条 Finding 的修复后验证状态只是发布依据之一，不会覆盖项目级门禁'), 'timeline must never replace project-level release authority');

assert(styles.includes('.finding-verification-timeline {'), 'timeline styles missing');
assert(styles.includes('.verification-change-badge {'), 'conclusion-change marker styles missing');
assert(styles.includes('@media (max-width: 560px)'), 'timeline must remain usable on mobile');

assert(packageJson.includes('"test:finding-verification-timeline": "node scripts/finding-verification-timeline-contract.mjs"'), 'package script missing verification timeline contract');
assert(ciGate.includes('"test:finding-verification-timeline"'), 'ci gate missing verification timeline contract');

console.log('finding verification timeline contract passed');
