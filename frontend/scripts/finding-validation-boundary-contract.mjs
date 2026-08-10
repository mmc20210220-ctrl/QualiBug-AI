import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const card = read('src/components/findings/FindingCard.tsx');
const verificationPanel = read('src/components/findings/FindingVerificationPanel.tsx');
const verification = read('src/lib/finding-verification.ts');
const findings = read('src/pages/Findings.tsx');
const replayPanel = read('src/components/evidence/ReplayPanel.tsx');
const replayViewer = read('src/components/ReplayViewer.tsx');
const data = read('src/api/data.ts');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

const forbiddenWorkflowTerms = [
  '人工处理状态',
  '负责人',
  '修复版本',
  '研发反馈',
  '外部任务链接',
  '保存协作记录',
  '风险接受 / 误报说明',
  '复制研发交接',
];
for (const term of forbiddenWorkflowTerms) {
  assert(!card.includes(term), `finding card must not manage enterprise workflow field: ${term}`);
}
assert(!card.includes('updateFindingCollaboration'), 'finding card must not write enterprise collaboration state');
assert(!card.includes("from '../../api/finding-collaboration'"), 'finding card must not depend on collaboration workflow API');
assert(card.includes('QualiBug 不记录企业内部负责人、修复版本、研发进度或工单流转'), 'finding card must state the product workflow boundary');
assert(card.includes('只判断验证结果，不管理修复过程'), 'finding card must remain a validation surface');
assert(card.includes('<FindingVerificationPanel'), 'finding card must delegate verification truth to the dedicated validation panel');

assert(verification.includes("export type FindingVerificationState ="), 'finding verification must have an explicit frontend presentation state');
assert(verification.includes("| 'verified_fixed'"), 'verification state must model a verified fix');
assert(verification.includes("| 'still_failing'"), 'verification state must model a still-failing issue');
assert(verification.includes("| 'inconclusive'"), 'verification state must model an inconclusive revalidation');
assert(verification.includes("| 'pending'"), 'verification state must model pending revalidation');
assert(verification.includes("| 'not_enrolled';"), 'verification state must model missing real obligations');
assert(verification.includes("latestStatus === 'passed' || latestStatus === 'verified_fixed'"), 'verified fix must come from a real backend terminal status');
assert(verification.includes("latestStatus === 'failed' || latestStatus === 'reopened' || gateStatus === 'failed'"), 'still failing must come from backend regression truth');
assert(verification.includes('INCONCLUSIVE_STATUSES.has(latestStatus)'), 'blocked or unverifiable runs must not become pass/fail');
assert(verification.includes('当前 Finding 没有真实回归义务'), 'missing regression obligation must fail closed');

assert(verificationPanel.includes('修复前基线'), 'verification panel must preserve original finding evidence');
assert(verificationPanel.includes('最新修复后验证'), 'verification panel must show the latest post-fix backend result');
assert(verificationPanel.includes('新原始证据</em><b>当前回执未提供'), 'missing post-fix raw evidence must be explicit');
assert(verificationPanel.includes('前端不会伪造“修复前后 Diff”'), 'frontend must not fabricate a before-after evidence package');
assert(verificationPanel.includes('客户修复后，重新验证'), 'validation surface must expose the product-owned closure action');
assert(verificationPanel.includes('不会记录负责人、修复版本或企业内部研发状态'), 'validation action must state the enterprise workflow boundary');

assert(findings.includes('const verificationRows = confirmed.map((finding) => ({ finding, verification: deriveFindingVerification(finding) }));'), 'findings summary must derive all validation buckets from one interpreter');
assert(findings.includes("verification.state === 'verified_fixed'"), 'findings must count verified fixes from the shared interpreter');
assert(findings.includes("verification.state === 'still_failing'"), 'findings must count still-failing issues independently');
assert(findings.includes("verification.state === 'inconclusive'"), 'findings must distinguish inconclusive validation');
assert(findings.includes("verification.state === 'pending'"), 'findings must distinguish pending validation without double-counting failures');
assert(findings.includes('修复后重新验证'), 'findings must expose a customer-facing revalidation action');
assert(findings.includes('不会提交空验证请求'), 'revalidation handler must fail closed without real obligations');
assert(findings.includes('QualiBug 只验证修复后的系统行为，不记录企业内部研发进度'), 'findings summary must preserve product scope');

assert(replayPanel.includes('修复后重新验证'), 'single-finding replay must be presented as post-fix validation');
assert(replayPanel.includes('重新验证当前问题'), 'single-finding replay must expose a clear validation action');
assert(replayPanel.includes('不会构造 synthetic Replay'), 'missing replay assets must fail closed');

assert(replayViewer.includes("import { emitScanCompleted } from '../api/data';"), 'replay must refresh frontend truth after backend validation');
assert(replayViewer.includes('emitScanCompleted(projectId);'), 'replay completion must trigger project data refresh');
assert(replayViewer.includes('问题仍可复现'), 'successful reproduction must be presented as the problem still existing');
assert(replayViewer.includes('本次未复现'), 'single replay non-reproduction must remain distinct from verified fixed');
assert(replayViewer.includes('一次“未复现”不能单独解释为已修复'), 'single replay must not auto-close a finding');
assert(replayViewer.includes('验证证据对比（问题发生时 vs 当前系统）'), 'replay must compare original evidence with the current system response');
assert(replayViewer.includes('不由前端根据差异自行推导“已修复”'), 'frontend diff must not override backend verification authority');

assert(data.includes("scope: 'defect_discovery_evidence_regression_release_status'"), 'customer finding projection must remain limited to validation scope');
assert(data.includes('不提供修复建议、修复方案或修复代码'), 'frontend data boundary must exclude implementation advice');

assert(packageJson.includes('"test:finding-validation-boundary": "node scripts/finding-validation-boundary-contract.mjs"'), 'package script missing finding validation boundary contract');
assert(ciGate.includes('"test:finding-validation-boundary"'), 'ci gate missing finding validation boundary contract');
assert(!packageJson.includes('"test:finding-collaboration"'), 'legacy enterprise collaboration contract must no longer be registered');
assert(!ciGate.includes('"test:finding-collaboration"'), 'ci gate must not protect enterprise workflow UI');

console.log('finding validation boundary contract passed');
