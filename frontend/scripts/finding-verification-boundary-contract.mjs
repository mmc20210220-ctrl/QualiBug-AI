import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const card = read('src/components/findings/FindingCard.tsx');
const panel = read('src/components/findings/FindingVerificationPanel.tsx');
const verification = read('src/lib/finding-verification.ts');
const findings = read('src/pages/Findings.tsx');
const evidence = read('src/pages/EvidenceChain.tsx');
const release = read('src/pages/ReleaseGate.tsx');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(!card.includes("from '../../api/finding-collaboration'"), 'customer finding card must not depend on enterprise collaboration workflow');
for (const forbidden of ['负责人', '修复版本', '研发反馈', '外部任务链接', '人工处理状态', '保存协作记录', '待分诊', '修复中']) {
  assert(!card.includes(forbidden), `finding card must not expose enterprise R&D workflow field: ${forbidden}`);
}
assert(!card.includes('updateFindingCollaboration('), 'finding card must not write enterprise collaboration state');
assert(card.includes('只判断验证结果，不管理修复过程'), 'finding card must state the verification-only product boundary');
assert(card.includes('复制问题摘要'), 'finding card may export facts without creating an enterprise workflow');
assert(card.includes('<FindingVerificationPanel'), 'finding card must expose the QualiBug verification closure');

assert(verification.includes("state: 'verified_fixed'"), 'verification interpreter must model verified repair');
assert(verification.includes("state: 'still_failing'"), 'verification interpreter must model continued failure');
assert(verification.includes("state: 'inconclusive'"), 'verification interpreter must model unverifiable runs');
assert(verification.includes("state: 'pending'"), 'verification interpreter must model pending re-verification');
assert(verification.includes("state: 'not_enrolled'"), 'verification interpreter must fail closed when no real obligation exists');
assert(verification.includes('前端不会构造 synthetic probe 或提交空验证'), 'verification interpreter must reject synthetic frontend verification');

assert(panel.includes('修复前基线'), 'verification panel must preserve the original finding baseline');
assert(panel.includes('最新修复后验证'), 'verification panel must show the latest real regression receipt');
assert(panel.includes('新原始证据</em><b>当前回执未提供'), 'missing post-fix raw evidence must be shown as unavailable');
assert(panel.includes('前端不会伪造“修复前后 Diff”'), 'verification panel must not fabricate before/after evidence');
assert(panel.includes('客户修复后，重新验证'), 'verification panel must expose the product-owned re-verification action');
assert(panel.includes('不会记录负责人、修复版本或企业内部研发状态'), 're-verification action must state the R&D workflow boundary');

assert(findings.includes('const verificationRows = confirmed.map((finding) => ({ finding, verification: deriveFindingVerification(finding) }));'), 'findings summary must use one verification interpreter');
assert(findings.includes("verification.state === 'verified_fixed'"), 'findings must count verified repairs separately');
assert(findings.includes("verification.state === 'still_failing'"), 'findings must count continued failures separately');
assert(findings.includes("verification.state === 'inconclusive'"), 'findings must count inconclusive verification separately');
assert(findings.includes("verification.state === 'pending'"), 'findings must count pending verification separately');
assert(findings.includes('修复后重新验证'), 'findings must frame regression as product re-verification');
assert(findings.includes('不需要先在 QualiBug 中登记负责人、版本或“修复中”状态'), 'findings must not require enterprise workflow state before verification');
assert(!findings.includes('onCollaborationUpdated='), 'findings must not wire enterprise collaboration callbacks');
assert(!findings.includes('project={project}'), 'finding card must not receive project solely for enterprise collaboration writes');

assert(evidence.includes('<FindingVerificationPanel finding={selected} />'), 'evidence center must show the same repair verification comparison');
assert(evidence.includes('回到这条问题并重新验证'), 'evidence center must route back to the exact finding for re-verification');

assert(release.includes('const requestedVerification = requestedFinding ? deriveFindingVerification(requestedFinding) : null;'), 'release review must surface exact finding verification state');
assert(release.includes('单条 Finding 的修复后验证状态只是发布依据之一，不会覆盖项目级门禁'), 'finding verification must never replace project-level release authority');
assert(release.includes('仅凭“列表中消失”不能断言已修复'), 'disappearing findings must not be inferred as fixed');
assert(release.includes('deriveReleasePresentation({'), 'release gate must remain driven by the project-level release interpreter');

assert(packageJson.includes('"test:finding-verification-boundary": "node scripts/finding-verification-boundary-contract.mjs"'), 'package script missing verification boundary contract');
assert(!packageJson.includes('"test:finding-collaboration"'), 'enterprise collaboration contract must no longer be a frontend product gate');
assert(ciGate.includes('"test:finding-verification-boundary"'), 'ci gate missing verification boundary contract');
assert(!ciGate.includes('"test:finding-collaboration"'), 'ci gate must not require enterprise collaboration UI');

console.log('finding verification boundary contract passed');
