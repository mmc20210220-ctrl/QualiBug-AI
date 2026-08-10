import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const verification = read('src/lib/finding-verification.ts');
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

assert(verification.includes('export function deriveVerificationRunPresentation('), 'history runs must reuse one shared status interpreter');
assert(verification.includes("outcome: 'fixed'"), 'passed history must map to a fixed outcome');
assert(verification.includes("outcome: 'open'"), 'failed history must map to an open outcome');
assert(verification.includes("outcome: 'unknown'"), 'inconclusive history must remain unknown');
assert(verification.includes('export function buildFindingVerificationTimeline(finding: Finding)'), 'verification timeline builder missing');
assert(verification.includes("kind: 'baseline'"), 'timeline must start from the original confirmed finding baseline');
assert(verification.includes(".sort((left, right) => String(left.generated_at || '').localeCompare(String(right.generated_at || '')))"), 'timeline must present real history in chronological order');
assert(verification.includes("const isKnownOutcome = presentation.outcome === 'fixed' || presentation.outcome === 'open';"), 'only terminal fixed/open outcomes may change the issue conclusion');
assert(verification.includes('const changedConclusion = isKnownOutcome && presentation.outcome !== lastKnownOutcome;'), 'conclusion changes must require a real terminal transition');
assert(verification.includes("presentation.outcome === 'unknown'\n        ? '本轮未形成可确认结论'"), 'inconclusive runs must not be presented as conclusion changes');
assert(verification.includes('if (isKnownOutcome) lastKnownOutcome = presentation.outcome;'), 'unknown runs must not overwrite the last known issue conclusion');

assert(verification.includes('export function deriveFocusedVerificationRunSummary('), 'focused verification run summary must have one shared derivation helper');
assert(verification.includes("event.kind === 'verification' && event.generatedAt === normalizedGeneratedAt"), 'focused summary must resolve only the exact real verification run');
assert(verification.includes("if (outcome === 'fixed' || outcome === 'open')"), 'focused summary must walk back to the previous known terminal conclusion');
assert(verification.includes("previousKnownOutcome = outcome;"), 'focused summary must preserve the last known fixed/open conclusion across unknown runs');
assert(verification.includes("event.outcome === 'open'"), 'focused summary must distinguish a still-open finding');
assert(verification.includes("event.outcome === 'fixed'"), 'focused summary must distinguish a verified fixed finding');
assert(verification.includes('单条问题通过不等于项目可以发布'), 'focused summary must not convert one fixed finding into project release approval');
assert(verification.includes('不能作为放行依据；项目级 Release Gate 仍需其他真实事实'), 'inconclusive focused runs must not become release evidence');

assert(verification.includes('export function deriveLatestVerificationRunSummary('), 'dashboard latest-run delta must have one shared derivation helper');
assert(verification.includes("item.kind === 'verification' && item.generatedAt === normalizedRunAt"), 'latest-run delta must only match finding history from the exact project run timestamp');
assert(verification.includes("event.changedConclusion && event.outcome === 'fixed'"), 'newly fixed count must require a real open-to-fixed transition');
assert(verification.includes("event.changedConclusion && event.outcome === 'open'"), 'reopened count must require a real fixed-to-open transition');
assert(verification.includes("!event.changedConclusion && event.outcome === 'open'"), 'still-failing count must stay separate from reopened findings');
assert(verification.includes("event.outcome === 'unknown'"), 'inconclusive latest-run outcomes must remain explicit');
assert(verification.includes("!event.changedConclusion && event.outcome === 'fixed'"), 'already-fixed findings that stay fixed must not be counted as newly fixed');

assert(evidencePresentation.includes("export function evidenceDeepLinkSearch(findingId: string, verificationAt = '')"), 'finding deep links must accept an optional exact verification run timestamp');
assert(evidencePresentation.includes("params.set('verification_at', normalizedVerificationAt)"), 'verification run timestamp must be encoded in the deep link only with a finding identity');

assert(timeline.includes("focusGeneratedAt?: string"), 'timeline must accept an exact verification run focus');
assert(timeline.includes("event.kind === 'verification' && event.generatedAt === normalizedFocus"), 'timeline focus must resolve only by exact real generated_at');
assert(timeline.includes("指定验证轮次 ${normalizedFocus} 不在当前 Finding 的真实 history 中；不会用其他轮次替代"), 'stale verification run links must fail closed');
assert(timeline.includes("focusedRef.current.scrollIntoView"), 'focused verification run must be brought into view');
assert(timeline.includes("verification-focused"), 'focused verification run must receive a distinct visual state');
assert(timeline.includes("if (focusedEvent && !recent.some"), 'compact history must explicitly preserve an older focused verification run');
assert(timeline.includes("visibleTimeline = [baseline, ...focusedAndRecent]"), 'compact release history must keep baseline plus focused and recent real runs');
assert(timeline.includes('中间 ${collapsedCount} 次已折叠'), 'collapsed history must remain explicit to the customer');
assert(timeline.includes('Probe {event.run.regression_probe_id}'), 'timeline must retain the exact regression probe identity');
assert(timeline.includes('{event.run.method} {event.run.path}'), 'timeline must retain the real validation target');

assert(runSummary.includes('deriveFocusedVerificationRunSummary(finding, normalizedGeneratedAt)'), 'focused summary component must consume the shared exact-run interpreter');
assert(runSummary.includes('上一已知结论'), 'focused summary must surface the previous known conclusion');
assert(runSummary.includes('本轮真实结果'), 'focused summary must surface the exact run result');
assert(runSummary.includes('是否改变结论'), 'focused summary must explain whether the finding conclusion actually changed');
assert(runSummary.includes('对发布的含义'), 'focused summary must explain the run impact without replacing project release authority');
assert(runSummary.includes('指定时间 {normalizedGeneratedAt} 不在当前 Finding 的真实验证历史中'), 'stale focused summary must fail closed');
assert(runSummary.includes('项目是否可以发布仍以项目级 Release Gate 为唯一权威'), 'focused summary must preserve project-level release authority');
assert(runSummary.includes('Probe {event.run.regression_probe_id}'), 'focused summary must retain exact probe identity');
assert(runSummary.includes('{event.run.method} {event.run.path}'), 'focused summary must retain exact validation target');

assert(dashboardDelta.includes('asText(regressionRun.generated_at) || asText(latestRun.generated_at)'), 'dashboard delta must anchor to the latest persisted project regression run');
assert(dashboardDelta.includes('deriveLatestVerificationRunSummary(findings, runAt)'), 'dashboard delta must use the shared exact-run finding derivation');
assert(dashboardDelta.includes('逐问题变化暂不可对齐'), 'missing per-finding run linkage must remain explicit');
assert(dashboardDelta.includes('前端不会把不同轮次的“最新状态”拼成一次验证变化，也不会补造修复数量'), 'dashboard must never fabricate a latest-run delta from mixed finding states');
assert(dashboardDelta.includes('const sortedRows = [...summary.rows].sort((left, right) => rowPriority(right) - rowPriority(left));'), 'dashboard drilldown must prioritize unresolved verification risk');
assert(dashboardDelta.includes('查看本轮具体 Finding（{summary.matchedCount}）'), 'dashboard must expose the concrete findings behind latest-run counts');
assert(dashboardDelta.includes('Finding {finding.id}'), 'dashboard drilldown must retain exact finding identity');
assert(dashboardDelta.includes('Probe {event.run.regression_probe_id}'), 'dashboard drilldown must retain exact regression probe identity');
assert(dashboardDelta.includes('evidenceDeepLinkSearch(finding.id, event.generatedAt)'), 'dashboard drilldown must preserve both exact finding and exact verification run identity');
assert(dashboardDelta.includes("navigateToProjectPath('/findings', project, findingSearch)"), 'dashboard drilldown must open the exact finding validation context');
assert(dashboardDelta.includes("navigateToProjectPath('/evidence', project, findingSearch)"), 'dashboard drilldown must open the exact finding evidence context');
assert(dashboardDelta.includes('const hasEvidence = (finding.evidence_chain?.length || 0) > 0;'), 'evidence CTA must only appear when the exact finding has evidence');

assert(findings.includes("params.get('verification_at')?.trim() || ''"), 'findings must consume the exact requested verification run');
assert(findings.includes("focusGeneratedAt={finding.id === requestedFindingId ? requestedVerificationAt : ''}"), 'only the exact requested finding may receive the requested run focus');
assert(findings.includes('requestedContextSearch = evidenceDeepLinkSearch(requestedFindingId, requestedVerificationAt)'), 'findings navigation must preserve exact finding and run context');
assert(findingCard.includes('focusGeneratedAt={focusGeneratedAt}'), 'finding card must pass exact run focus into the verification panel');
assert(verificationPanel.includes('<FindingVerificationRunSummary finding={finding} generatedAt={focusGeneratedAt} />'), 'finding and evidence detail must show the focused run change summary');
assert(verificationPanel.includes('<FindingVerificationTimeline finding={finding} focusGeneratedAt={focusGeneratedAt} />'), 'finding verification panel must pass exact run focus into the real timeline');
assert(evidenceDrawer.includes('evidenceDeepLinkSearch(finding.id, focusGeneratedAt)'), 'evidence drawer must preserve exact run when opening the full evidence center');

assert(evidence.includes("params.get('verification_at')?.trim() || ''"), 'evidence center must consume the exact requested verification run');
assert(evidence.includes("next.delete('verification_at')"), 'manual evidence selection must clear a verification run belonging to another finding');
assert(evidence.includes("focusGeneratedAt={preserveRequestedRun ? requestedVerificationAt : ''}"), 'evidence center must focus the exact run only for the exact requested finding');
assert(evidence.includes("navigateToProjectPath('/release', project, findingContextSearch)"), 'evidence to release must preserve exact finding and verification run context');

assert(release.includes("params.get('verification_at')?.trim() || ''"), 'release review must consume the exact requested verification run');
assert(release.includes('findingContextSearch = evidenceDeepLinkSearch(requestedFindingId, requestedVerificationAt)'), 'release navigation must preserve exact finding and run context');
assert(release.includes('<FindingVerificationRunSummary finding={requestedFinding} generatedAt={requestedVerificationAt} />'), 'release review must show the same focused run change summary');
assert(release.includes('focusGeneratedAt={requestedVerificationAt}'), 'release compact timeline must focus the requested real verification run');
assert(release.includes('单条 Finding 的修复后验证状态只是发布依据之一，不会覆盖项目级门禁'), 'focused timeline must never replace project-level release authority');

assert(gateBanner.includes('<DashboardVerificationDeltaPanel record={record} project={project} />'), 'dashboard gate area must surface the latest-run finding delta');
assert(gateBanner.includes('验证变化不是发布结论，发布 Gate 也不能伪造逐 Finding 变化'), 'dashboard must preserve separation between finding deltas and project release authority');

assert(styles.includes('.verification-timeline-item.verification-focused'), 'focused verification run styles missing');
assert(styles.includes('.verification-focus-badge'), 'focused verification run badge styles missing');
assert(styles.includes('.verification-focus-hint.warning'), 'stale run focus warning styles missing');
assert(styles.includes('.verification-run-summary {'), 'focused verification summary styles missing');
assert(styles.includes('.verification-run-summary-grid {'), 'focused verification summary grid styles missing');
assert(styles.includes('grid-template-columns: minmax(0, 1fr);'), 'focused verification summary must collapse to one column on mobile');
assert(styles.includes('.verification-delta-row .settings-actions .btn'), 'dashboard delta actions must remain usable on mobile');
assert(styles.includes('@media (max-width: 560px)'), 'timeline must remain usable on mobile');

assert(packageJson.includes('"test:finding-verification-timeline": "node scripts/finding-verification-timeline-contract.mjs"'), 'package script missing verification timeline contract');
assert(ciGate.includes('"test:finding-verification-timeline"'), 'ci gate missing verification timeline contract');

console.log('finding verification timeline contract passed');
