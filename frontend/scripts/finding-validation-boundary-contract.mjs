import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const card = read('src/components/findings/FindingCard.tsx');
const verificationPanel = read('src/components/findings/FindingVerificationPanel.tsx');
const verificationRunSummary = read('src/components/findings/FindingVerificationRunSummary.tsx');
const verificationStatus = read('src/components/findings/FindingVerificationStatus.tsx');
const dashboardFocus = read('src/components/dashboard/DashboardFocusFindingCard.tsx');
const verificationStyles = read('src/styles/finding-verification.css');
const main = read('src/main.tsx');
const verification = read('src/lib/finding-verification.ts');
const findings = read('src/pages/Findings.tsx');
const dashboard = read('src/pages/Dashboard.tsx');
const evidence = read('src/pages/EvidenceChain.tsx');
const release = read('src/pages/ReleaseGate.tsx');
const replayPanel = read('src/components/evidence/ReplayPanel.tsx');
const replayViewer = read('src/components/ReplayViewer.tsx');
const data = read('src/api/data.ts');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

const forbiddenWorkflowControls = [
  '<span>人工处理状态</span>',
  '<span>负责人</span>',
  '<span>修复版本</span>',
  '<span>研发反馈</span>',
  '<span>外部任务链接（可选）</span>',
  '保存协作记录',
  '风险接受 / 误报说明',
  '复制研发交接',
];
for (const snippet of forbiddenWorkflowControls) {
  assert(!card.includes(snippet), `finding card must not expose enterprise workflow control: ${snippet}`);
}
assert(!card.includes('updateFindingCollaboration'), 'finding card must not write enterprise collaboration state');
assert(!card.includes("from '../../api/finding-collaboration'"), 'finding card must not depend on collaboration workflow API');
assert(card.includes('QualiBug 不记录企业内部负责人、修复版本、研发进度或工单流转'), 'finding card must state the product workflow boundary');
assert(card.includes('只判断验证结果，不管理修复过程'), 'finding card must remain a validation surface');
assert(card.includes('<FindingVerificationPanel'), 'finding card must delegate verification truth to the dedicated validation panel');
assert(card.includes('<FindingVerificationStatus finding={finding} compact />'), 'finding card summary must use the shared verification status component');
assert(!card.includes('function regressionStatusLabel'), 'finding card must not keep a second verification label interpreter');
assert(!card.includes('function regressionTone'), 'finding card must not keep a second verification tone interpreter');

assert(verification.includes('export type FindingVerificationState ='), 'finding verification must have an explicit frontend presentation state');
assert(verification.includes("| 'verified_fixed'"), 'verification state must model a verified fix');
assert(verification.includes("| 'still_failing'"), 'verification state must model a still-failing issue');
assert(verification.includes("| 'inconclusive'"), 'verification state must model an inconclusive revalidation');
assert(verification.includes("| 'pending'"), 'verification state must model pending revalidation');
assert(verification.includes("| 'not_enrolled';"), 'verification state must model missing real obligations');
assert(verification.includes('priority: number;'), 'shared verification presentation must own customer attention priority');
assert(verification.includes('nextActionLabel: string;'), 'shared verification presentation must own validation next-action wording');
assert(verification.includes('priority: 50'), 'still-failing verification must have the highest customer attention priority');
assert(verification.includes('priority: 40'), 'inconclusive verification must outrank pending verification');
assert(verification.includes('priority: 30'), 'pending verification must remain actionable');
assert(verification.includes('priority: 20'), 'not-enrolled verification must remain below active validation risk');
assert(verification.includes('priority: 10'), 'verified-fixed state must fall below unresolved validation risk');
assert(verification.includes("latestStatus === 'passed' || latestStatus === 'verified_fixed'"), 'verified fix must come from a real backend terminal status');
assert(verification.includes("latestStatus === 'failed' || latestStatus === 'reopened' || gateStatus === 'failed'"), 'still failing must come from backend regression truth');
assert(verification.includes('INCONCLUSIVE_STATUSES.has(latestStatus)'), 'blocked or unverifiable runs must not become pass/fail');
assert(verification.includes('当前 Finding 没有真实回归义务'), 'missing regression obligation must fail closed');
assert(verification.includes('deriveFocusedVerificationRunSummary'), 'focused verification context must reuse the shared timeline truth');
assert(verification.includes('单条问题通过不等于项目可以发布'), 'one verified finding must never imply project release approval');

assert(verificationStatus.includes('deriveFindingVerification(finding)'), 'shared status component must consume the one verification interpreter');
assert(verificationStatus.includes('verification-${presentation.tone}'), 'shared status component must derive visual tone from the interpreter');
assert(verificationStatus.includes('data-verification-state={presentation.state}'), 'shared status component must expose the exact semantic state');
assert(main.includes("import './styles/finding-verification.css';"), 'shared verification status styles must be loaded globally');
assert(verificationStyles.includes('var(--success-muted)'), 'verified-fixed status must use the shared success token');
assert(verificationStyles.includes('var(--danger-muted)'), 'still-failing status must use the shared danger token');
assert(verificationStyles.includes('var(--warning-muted)'), 'pending and inconclusive status must use the shared warning token');
assert(verificationStyles.includes('verification-neutral'), 'not-enrolled state must have a neutral presentation');

assert(verificationPanel.includes('修复前基线'), 'verification panel must preserve original finding evidence');
assert(verificationPanel.includes('最新修复后验证'), 'verification panel must show the latest post-fix backend result');
assert(verificationPanel.includes('新原始证据</em><b>当前回执未提供'), 'missing post-fix raw evidence must be explicit');
assert(verificationPanel.includes('前端不会伪造“修复前后 Diff”'), 'frontend must not fabricate a before-after evidence package');
assert(verificationPanel.includes('客户修复后，重新验证'), 'validation surface must expose the product-owned closure action');
assert(verificationPanel.includes('不会记录负责人、修复版本或企业内部研发状态'), 'validation action must state the enterprise workflow boundary');
assert(verificationPanel.includes('<FindingVerificationStatus finding={finding} />'), 'verification panel header must use the shared status component');
assert(verificationPanel.includes('<FindingVerificationRunSummary finding={finding} generatedAt={focusGeneratedAt} />'), 'focused finding/evidence context must explain the exact run before showing history');
assert(verificationRunSummary.includes('上一已知结论'), 'focused run summary must explain prior validation truth');
assert(verificationRunSummary.includes('本轮真实结果'), 'focused run summary must explain exact-run truth');
assert(verificationRunSummary.includes('项目是否可以发布仍以项目级 Release Gate 为唯一权威'), 'focused run summary must remain validation-only');

assert(findings.includes('const verificationRows = confirmed.map((finding) => ({ finding, verification: deriveFindingVerification(finding) }));'), 'findings summary must derive all validation buckets from one interpreter');
assert(findings.includes("verification.state === 'verified_fixed'"), 'findings must count verified fixes from the shared interpreter');
assert(findings.includes("verification.state === 'still_failing'"), 'findings must count still-failing issues independently');
assert(findings.includes("verification.state === 'inconclusive'"), 'findings must distinguish inconclusive validation');
assert(findings.includes("verification.state === 'pending'"), 'findings must distinguish pending validation without double-counting failures');
assert(findings.includes('deriveFindingVerification(right).priority - deriveFindingVerification(left).priority'), 'findings must order unresolved verification risk through the shared priority');
assert(findings.includes('修复后重新验证'), 'findings must expose a customer-facing revalidation action');
assert(findings.includes('不会提交空验证请求'), 'revalidation handler must fail closed without real obligations');
assert(findings.includes('QualiBug 只验证修复后的系统行为，不记录企业内部研发进度'), 'findings summary must preserve product scope');

assert(dashboard.includes('deriveFindingVerification(right).priority - deriveFindingVerification(left).priority'), 'dashboard focus must use the shared verification priority before severity tie-breaking');
assert(dashboard.includes('<DashboardFocusFindingCard key={finding.id} finding={finding} project={project} />'), 'dashboard focus must delegate exact finding presentation to the shared focus card');
assert(dashboardFocus.includes('<FindingVerificationStatus finding={finding} />'), 'dashboard focus card must expose the shared verification status');

assert(evidence.includes('<FindingVerificationStatus finding={finding} compact />'), 'evidence list must expose the shared verification status');
assert(evidence.includes('<FindingVerificationStatus finding={selected} />'), 'selected evidence detail must expose the shared verification status');
assert(evidence.includes('<FindingVerificationPanel'), 'evidence must preserve the detailed verification comparison');
assert(evidence.includes("focusGeneratedAt={preserveRequestedRun ? requestedVerificationAt : ''}"), 'evidence must pass the exact run focus into the shared verification panel');

assert(release.includes('<FindingVerificationStatus finding={requestedFinding} />'), 'release finding context must expose the shared verification status');
assert(release.includes('<FindingVerificationRunSummary finding={requestedFinding} generatedAt={requestedVerificationAt} />'), 'release review must reuse the focused run summary');
assert(release.includes('requestedVerification.nextActionLabel'), 'release finding context must expose the shared validation next action');
assert(release.includes('单条 Finding 的修复后验证状态只是发布依据之一，不会覆盖项目级门禁'), 'finding verification must never replace project-level release authority');
assert(release.includes('deriveReleasePresentation({'), 'release gate must remain driven by the project-level release interpreter');

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
