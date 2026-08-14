import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const verification = read('src/lib/finding-verification.ts');
const focusContext = read('src/lib/finding-verification-focus.ts');
const evidencePresentation = read('src/lib/evidence-presentation.ts');
const timeline = read('src/components/findings/FindingVerificationTimeline.tsx');
const runSummary = read('src/components/findings/FindingVerificationRunSummary.tsx');
const verificationPanel = read('src/components/findings/FindingVerificationPanel.tsx');
const findingCard = read('src/components/findings/FindingCard.tsx');
const evidenceDrawer = read('src/components/findings/EvidenceDrawer.tsx');
const dashboardDelta = read('src/components/dashboard/DashboardVerificationDeltaPanel.tsx');
const gateBanner = read('src/components/dashboard/RegressionGateBanner.tsx');
const findings = read('src/pages/Findings.tsx');
const evidence = read('src/pages/EvidenceChain.tsx');
const release = read('src/pages/ReleaseGate.tsx');
const styles = read('src/styles/finding-verification.css');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

// Shared truth interpreter and chronological timeline.
assert(verification.includes('export function deriveVerificationRunPresentation('), 'history runs must reuse one shared status interpreter');
assert(verification.includes("outcome: 'fixed'"), 'passed history must map to fixed');
assert(verification.includes("outcome: 'open'"), 'failed history must map to open');
assert(verification.includes("outcome: 'unknown'"), 'inconclusive history must remain unknown');
assert(verification.includes('export function buildFindingVerificationTimeline(finding: Finding)'), 'verification timeline builder missing');
assert(verification.includes("kind: 'baseline'"), 'timeline must start from original finding baseline');
assert(verification.includes(".sort((left, right) => String(left.generated_at || '').localeCompare(String(right.generated_at || '')))"), 'history must be chronological');
assert(verification.includes('const isKnownOutcome = isKnownVerificationOutcome(presentation.outcome);'), 'only fixed/open may change conclusion');
assert(verification.includes('const changedConclusion = isKnownOutcome && presentation.outcome !== lastKnownOutcome;'), 'conclusion changes require a terminal transition');
assert(verification.includes('if (isKnownVerificationOutcome(presentation.outcome)) {'), 'unknown must not overwrite last known conclusion');

// Exact focused run truth remains owned by the original shared interpreter.
assert(verification.includes('export function deriveFocusedVerificationRunSummary('), 'exact focused run helper missing');
assert(verification.includes("event.kind === 'verification' && event.generatedAt === normalizedGeneratedAt"), 'focused run must match exact generated_at');
assert(verification.includes("if (outcome === 'fixed' || outcome === 'open')"), 'focused run must walk back to previous terminal conclusion');
assert(verification.includes('单条问题通过不等于项目可以发布'), 'one finding pass must not imply project release');
assert(verification.includes('不能作为放行依据；项目级 Release Gate 仍需其他真实事实'), 'unknown run must not become release evidence');

// Historical/latest classification layers on top of exact-run truth, never replaces it.
assert(focusContext.includes('deriveFocusedVerificationRunSummary(finding, normalizedGeneratedAt)'), 'focus context must reuse exact-run truth');
assert(focusContext.includes('buildFindingVerificationTimeline(finding)'), 'focus context must reuse real timeline');
assert(focusContext.includes(".filter((event) => event.kind === 'verification')"), 'latest event must exclude baseline');
assert(focusContext.includes('latestEvent && latestEvent.key === summary.event.key'), 'latest/history identity must compare exact timeline events');
assert(!focusContext.includes('Date.now('), 'focus context must not use time windows');
assert(!focusContext.includes('Math.abs('), 'focus context must not use nearest-time heuristics');

// Latest project run delta must remain exact-run only.
assert(verification.includes('export function deriveLatestVerificationRunSummary('), 'dashboard latest-run delta helper missing');
assert(verification.includes("item.kind === 'verification' && item.generatedAt === normalizedRunAt"), 'dashboard delta must exact-match project run timestamp');
assert(verification.includes("event.changedConclusion && event.outcome === 'fixed'"), 'newly fixed requires open-to-fixed');
assert(verification.includes("event.changedConclusion && event.outcome === 'open'"), 'reopened requires fixed-to-open');
assert(verification.includes("!event.changedConclusion && event.outcome === 'open'"), 'still failing must remain separate');
assert(verification.includes("event.outcome === 'unknown'"), 'inconclusive must remain explicit');

// URL identity propagation.
assert(evidencePresentation.includes("export function evidenceDeepLinkSearch(findingId: string, verificationAt = '')"), 'deep links must accept exact verification timestamp');
assert(evidencePresentation.includes("params.set('verification_at', normalizedVerificationAt)"), 'verification timestamp must be encoded with finding context');
assert(findings.includes("params.get('verification_at')?.trim() || ''"), 'findings must consume verification_at');
assert(evidence.includes("params.get('verification_at')?.trim() || ''"), 'evidence must consume verification_at');
assert(release.includes("params.get('verification_at')?.trim() || ''"), 'release must consume verification_at');
assert(evidence.includes("next.delete('verification_at')"), 'manual evidence finding switch must clear stale verification_at');

// Timeline exact focus and fail-closed behavior.
assert(timeline.includes('focusGeneratedAt?: string'), 'timeline must accept exact run focus');
assert(timeline.includes("event.kind === 'verification' && event.generatedAt === normalizedFocus"), 'timeline focus must exact-match generated_at');
assert(timeline.includes('不在当前 Finding 的真实 history 中；不会用其他轮次替代'), 'stale run must fail closed');
assert(timeline.includes('focusedRef.current.scrollIntoView'), 'focused run must be brought into view');
assert(timeline.includes('verification-focused'), 'focused run must have distinct visual state');
assert(timeline.includes('if (focusedEvent && !recent.some'), 'compact history must retain an older focused run');
assert(timeline.includes('visibleTimeline = [baseline, ...focusedAndRecent]'), 'compact history must keep baseline + focus + recent');
assert(timeline.includes('中间 ${collapsedCount} 次已折叠'), 'collapsed history count must remain explicit');
assert(timeline.includes('Probe {event.run.regression_probe_id}'), 'timeline must retain probe identity');
assert(timeline.includes('{event.run.method} {event.run.path}'), 'timeline must retain validation target');

// Focused summary must now distinguish historical from current truth.
assert(runSummary.includes('deriveFindingVerificationFocusContext(finding, normalizedGeneratedAt)'), 'summary must consume shared historical/latest focus context');
assert(runSummary.includes("isLatestRun ? '当前最新验证' : '历史验证轮次'"), 'summary must label latest vs historical');
assert(runSummary.includes('上一已知结论'), 'summary must show prior known conclusion');
assert(runSummary.includes('当前最新真实结果'), 'latest focused run must be explicitly current');
assert(runSummary.includes('历史本轮真实结果'), 'historical focused run must be explicitly historical');
assert(runSummary.includes('你正在查看历史轮次'), 'historical focus warning missing');
assert(runSummary.includes('下方本轮结果不会覆盖当前最新结论'), 'historical run must not impersonate current state');
assert(runSummary.includes('当前发布判断应结合该 Finding 的最新真实验证结论与项目级 Release Gate'), 'historical release meaning must defer to current truth + project gate');
assert(runSummary.includes('指定时间 {normalizedGeneratedAt} 不在当前 Finding 的真实验证历史中'), 'stale summary must fail closed');
assert(runSummary.includes('Probe {event.run.regression_probe_id}'), 'summary must retain exact probe identity');
assert(runSummary.includes('{event.run.method} {event.run.path}'), 'summary must retain exact target');

// Shared panel and page integrations.
assert(verificationPanel.includes('当前最新结论'), 'verification panel must explicitly label current status');
assert(verificationPanel.includes('const viewingHistoricalRun = Boolean(focusContext && !focusContext.isLatestRun);'), 'panel must model historical focus');
assert(verificationPanel.includes('你当前定位的是历史验证轮次'), 'panel must explain historical context');
assert(verificationPanel.includes('<FindingVerificationRunSummary finding={finding} generatedAt={focusGeneratedAt} />'), 'finding/evidence detail must show focused summary');
assert(verificationPanel.includes('<FindingVerificationTimeline finding={finding} focusGeneratedAt={focusGeneratedAt} />'), 'panel must pass exact focus to timeline');
assert(findingCard.includes('focusGeneratedAt={focusGeneratedAt}'), 'finding card must pass focus into verification panel');
assert(evidenceDrawer.includes('evidenceDeepLinkSearch(finding.id, focusGeneratedAt)'), 'drawer must preserve exact run to evidence center');
assert(evidence.includes("focusGeneratedAt={preserveRequestedRun ? requestedVerificationAt : ''}"), 'evidence must focus run only for exact finding');
assert(release.includes('<FindingVerificationRunSummary finding={requestedFinding} generatedAt={requestedVerificationAt} />'), 'release must reuse historical-aware summary');
assert(release.includes('focusGeneratedAt={requestedVerificationAt}'), 'release compact timeline must focus exact run');
assert(release.includes('不会覆盖项目级门禁'), 'single finding context must not replace project gate');

// Dashboard latest-run drilldown remains exact and evidence-aware.
assert(dashboardDelta.includes('deriveLatestVerificationRunSummary(findings, runAt)'), 'dashboard must use shared exact-run delta');
assert(dashboardDelta.includes('逐问题变化暂不可对齐'), 'missing per-finding linkage must remain explicit');
assert(dashboardDelta.includes('前端不会把不同轮次的“最新状态”拼成一次验证变化，也不会补造修复数量'), 'dashboard must not synthesize mixed-run delta');
assert(dashboardDelta.includes('evidenceDeepLinkSearch(finding.id, event.generatedAt)'), 'dashboard must preserve exact finding + run');
assert(dashboardDelta.includes("navigateToProjectPath('/findings', project, findingSearch)"), 'dashboard must open exact finding context');
assert(dashboardDelta.includes("navigateToProjectPath('/evidence', project, findingSearch)"), 'dashboard must open exact evidence context');
assert(gateBanner.includes('<DashboardVerificationDeltaPanel record={record} project={project} />'), 'dashboard gate area must surface run delta');
assert(gateBanner.includes('验证变化不是发布结论，发布 Gate 也不能伪造逐 Finding 变化'), 'dashboard must separate finding delta from release authority');

// Existing focus styles and mobile behavior stay required.
assert(styles.includes('.verification-timeline-item.verification-focused'), 'focused run styles missing');
assert(styles.includes('.verification-focus-badge'), 'focus badge styles missing');
assert(styles.includes('.verification-focus-hint.warning'), 'stale focus warning styles missing');
assert(styles.includes('.verification-run-summary {'), 'run summary styles missing');
assert(styles.includes('.verification-run-summary-grid {'), 'run summary grid styles missing');
assert(styles.includes('@media (max-width: 560px)'), 'verification UI must remain mobile-safe');

assert(packageJson.includes('"test:finding-verification-timeline": "node scripts/finding-verification-timeline-contract.mjs"'), 'package script missing timeline contract');
assert(packageJson.includes('"test:finding-verification-focus": "node scripts/finding-verification-focus-contract.mjs"'), 'package script missing focus contract');
assert(ciGate.includes('"test:finding-verification-timeline"'), 'ci gate missing timeline contract');
assert(ciGate.includes('"test:finding-verification-focus"'), 'ci gate missing focus contract');

console.log('finding verification timeline contract passed');
