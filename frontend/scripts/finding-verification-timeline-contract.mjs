import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const verification = read('src/lib/finding-verification.ts');
const timeline = read('src/components/findings/FindingVerificationTimeline.tsx');
const verificationPanel = read('src/components/findings/FindingVerificationPanel.tsx');
const dashboardDelta = read('src/components/dashboard/DashboardVerificationDeltaPanel.tsx');
const gateBanner = read('src/components/dashboard/RegressionGateBanner.tsx');
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

assert(verification.includes('export function deriveLatestVerificationRunSummary('), 'dashboard latest-run delta must have one shared derivation helper');
assert(verification.includes("item.kind === 'verification' && item.generatedAt === normalizedRunAt"), 'latest-run delta must only match finding history from the exact project run timestamp');
assert(verification.includes("event.changedConclusion && event.outcome === 'fixed'"), 'newly fixed count must require a real open-to-fixed transition');
assert(verification.includes("event.changedConclusion && event.outcome === 'open'"), 'reopened count must require a real fixed-to-open transition');
assert(verification.includes("!event.changedConclusion && event.outcome === 'open'"), 'still-failing count must stay separate from reopened findings');
assert(verification.includes("event.outcome === 'unknown'"), 'inconclusive latest-run outcomes must remain explicit');
assert(verification.includes("!event.changedConclusion && event.outcome === 'fixed'"), 'already-fixed findings that stay fixed must not be counted as newly fixed');

assert(timeline.includes('真实验证历史'), 'timeline must identify itself as real validation history');
assert(timeline.includes('最近结论变化：${latestChange.transitionLabel}'), 'timeline must surface the latest real conclusion transition');
assert(timeline.includes('结论变化'), 'timeline must visibly mark the run that changed the issue conclusion');
assert(timeline.includes('Probe {event.run.regression_probe_id}'), 'timeline must retain the exact regression probe identity');
assert(timeline.includes('{event.run.method} {event.run.path}'), 'timeline must retain the real validation target');
assert(timeline.includes('后端尚未返回真实修复后验证历史。前端不会补造验证轮次'), 'missing history must remain explicit instead of synthetic');
assert(timeline.includes('const hasCollapsedHistory = compact && timeline.length > 4;'), 'compact history must have an explicit folding rule');
assert(timeline.includes('? [timeline[0], ...timeline.slice(-3)]'), 'compact release history must preserve the original finding baseline and latest runs');
assert(timeline.includes('中间 {collapsedCount} 次已折叠'), 'collapsed history must be explicit to the customer');

assert(dashboardDelta.includes('asText(regressionRun.generated_at) || asText(latestRun.generated_at)'), 'dashboard delta must anchor to the latest persisted project regression run');
assert(dashboardDelta.includes('deriveLatestVerificationRunSummary(findings, runAt)'), 'dashboard delta must use the shared exact-run finding derivation');
assert(dashboardDelta.includes('逐问题变化暂不可对齐'), 'missing per-finding run linkage must remain explicit');
assert(dashboardDelta.includes('前端不会把不同轮次的“最新状态”拼成一次验证变化，也不会补造修复数量'), 'dashboard must never fabricate a latest-run delta from mixed finding states');
assert(dashboardDelta.includes('<em>刚验证修复</em><b>{summary.fixedCount}</b>'), 'dashboard must expose newly verified fixes from real conclusion changes');
assert(dashboardDelta.includes('<em>重新出现</em><b>{summary.reopenedCount}</b>'), 'dashboard must expose real reopened findings separately');
assert(dashboardDelta.includes('<em>仍失败</em><b>{summary.stillFailingCount}</b>'), 'dashboard must expose continuing failures separately');
assert(dashboardDelta.includes('<em>无法确认</em><b>{summary.inconclusiveCount}</b>'), 'dashboard must expose inconclusive latest-run findings');
assert(dashboardDelta.includes('<em>保持通过</em><b>{summary.keptFixedCount}</b>'), 'dashboard must distinguish already-fixed findings that stayed fixed');
assert(dashboardDelta.includes('“刚验证修复 / 重新出现”只来自真实 open ↔ fixed 结论变化'), 'dashboard delta must explain its strict conclusion-change rule');
assert(dashboardDelta.includes('const sortedRows = [...summary.rows].sort((left, right) => rowPriority(right) - rowPriority(left));'), 'dashboard drilldown must prioritize unresolved verification risk');
assert(dashboardDelta.includes('查看本轮具体 Finding（{summary.matchedCount}）'), 'dashboard must expose the concrete findings behind latest-run counts');
assert(dashboardDelta.includes('Finding {finding.id}'), 'dashboard drilldown must retain exact finding identity');
assert(dashboardDelta.includes('Probe {event.run.regression_probe_id}'), 'dashboard drilldown must retain exact regression probe identity');
assert(dashboardDelta.includes('{event.run.method} {event.run.path}'), 'dashboard drilldown must retain the real validation target');
assert(dashboardDelta.includes('evidenceDeepLinkSearch(finding.id)'), 'dashboard drilldown must deep-link by exact finding ID');
assert(dashboardDelta.includes("navigateToProjectPath('/findings', project, findingSearch)"), 'dashboard drilldown must open the exact finding validation context');
assert(dashboardDelta.includes("navigateToProjectPath('/evidence', project, findingSearch)"), 'dashboard drilldown must open the exact finding evidence context');
assert(dashboardDelta.includes('const hasEvidence = (finding.evidence_chain?.length || 0) > 0;'), 'evidence CTA must only appear when the exact finding has evidence');
assert(dashboardDelta.includes('首屏仅展示风险最高的 8 条'), 'large latest-run result sets must disclose first-screen folding');

assert(gateBanner.includes('<DashboardVerificationDeltaPanel record={record} project={project} />'), 'dashboard gate area must surface the latest-run finding delta');
assert(gateBanner.includes('{deltaPanel}'), 'verification delta must remain visible alongside blocking gate facts');
assert(gateBanner.includes('if (!shouldBlockFirstScreen) return deltaPanel;'), 'a passing gate must not hide useful verification change value');
assert(gateBanner.includes('验证变化不是发布结论，发布 Gate 也不能伪造逐 Finding 变化'), 'dashboard must preserve separation between finding deltas and project release authority');

assert(verificationPanel.includes('<FindingVerificationTimeline finding={finding} />'), 'finding/evidence detailed validation must show the complete real timeline');
assert(evidence.includes('<FindingVerificationPanel finding={selected} />'), 'evidence center must inherit the complete timeline for the exact finding');
assert(release.includes('<FindingVerificationTimeline finding={requestedFinding} compact />'), 'release finding context must show a compact real conclusion timeline');
assert(release.includes('单条 Finding 的修复后验证状态只是发布依据之一，不会覆盖项目级门禁'), 'timeline must never replace project-level release authority');

assert(styles.includes('.finding-verification-timeline {'), 'timeline styles missing');
assert(styles.includes('.verification-change-badge {'), 'conclusion-change marker styles missing');
assert(styles.includes('.verification-delta-row {'), 'dashboard delta drilldown styles missing');
assert(styles.includes('.verification-delta-row .settings-actions .btn'), 'dashboard delta actions must remain usable on mobile');
assert(styles.includes('@media (max-width: 560px)'), 'timeline must remain usable on mobile');

assert(packageJson.includes('"test:finding-verification-timeline": "node scripts/finding-verification-timeline-contract.mjs"'), 'package script missing verification timeline contract');
assert(ciGate.includes('"test:finding-verification-timeline"'), 'ci gate missing verification timeline contract');

console.log('finding verification timeline contract passed');
