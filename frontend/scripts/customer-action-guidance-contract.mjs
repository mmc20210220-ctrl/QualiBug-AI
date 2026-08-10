import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const app = read('src/App.tsx');
const dashboard = read('src/pages/Dashboard.tsx');
const dashboardFocus = read('src/components/dashboard/DashboardFocusFindingCard.tsx');
const releasePresentation = read('src/lib/release-presentation.ts');
const releaseGate = read('src/pages/ReleaseGate.tsx');
const runCenter = read('src/pages/EnterpriseCampaigns.tsx');
const runPreflight = read('src/lib/run-preflight-presentation.ts');
const runPreflightSnapshot = read('src/components/run/RunPreflightDecisionSnapshot.tsx');
const findings = read('src/pages/Findings.tsx');
const evidence = read('src/pages/EvidenceChain.tsx');
const evidenceDrawer = read('src/components/findings/EvidenceDrawer.tsx');
const verificationPanel = read('src/components/findings/FindingVerificationPanel.tsx');
const evidencePresentation = read('src/lib/evidence-presentation.ts');
const projectNavigation = read('src/lib/project-navigation.ts');
const coverage = read('src/pages/CoverageMatrix.tsx');
const layout = read('src/components/Layout.tsx');
const sidebar = read('src/components/Sidebar.tsx');
const topbar = read('src/components/Topbar.tsx');
const responsive = read('src/styles/customer-responsive.css');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(app.includes('function PreserveSearchRedirect({ to }: { to: string })'), 'route fallbacks must preserve query context');
assert(app.includes('return <Navigate to={`${to}${location.search}`} replace />;'), 'redirects must preserve project context');
assert(projectNavigation.includes("const navigateToProjectPath = useCallback((pathname: string, projectId?: string, currentSearch = '') =>"), 'project navigation must preserve entity context');

assert(dashboard.includes('const resultIncomplete = pipelineUnhealthy || campaignBlocked || coverageDeferred;'), 'dashboard must have one incomplete-result authority');
assert(dashboard.includes('当前 0 个 P0 只代表已覆盖部分，不能直接推导为安全'), 'partial coverage must never imply safety');
assert(dashboard.includes('hasFindingReverificationObligation'), 'dashboard must reuse shared re-verification obligation authority');
assert(dashboard.includes('不会提交空验证请求'), 'dashboard validation must fail closed without real obligations');
assert(dashboardFocus.includes('查看这条验证'), 'dashboard exact finding action must use validation language');
assert(!dashboardFocus.includes('处理这条问题'), 'dashboard must not frame a finding as an enterprise task');

assert(releasePresentation.includes('export function deriveReleasePresentation'), 'release truth must use one shared interpreter');
assert(releasePresentation.includes("gateOverall === 'pass'"), 'green release state must require explicit pass');
assert(releasePresentation.includes('尚未取得完整发布门禁回执'), 'missing gate data must not imply safety');
assert(releaseGate.includes('deriveReleasePresentation({'), 'release page must remain project-gate driven');
assert(releaseGate.includes('单条 Finding 的修复后验证状态只是发布依据之一，不会覆盖项目级门禁'), 'single finding validation must not replace release authority');
assert(releaseGate.includes('仅凭“列表中消失”不能断言已修复'), 'missing finding must not be inferred as fixed');

assert(runCenter.includes('const runBlockedByPreflight = !loadingPreflight && !preflightReady;'), 'run center must retain explicit preflight blocking');
assert(runCenter.includes('deriveRunPreflightPresentation({'), 'run center must delegate customer-facing readiness wording to the shared preflight interpreter');
assert(runCenter.includes('runDisabled={runDisabled}'), 'run snapshot must receive the same submission lock used by the run center');
assert(runCenter.includes("if (!preflightReady)"), 'run submission handler must still fail closed on backend preflight');
assert(runPreflight.includes('const preflightReady = Boolean(input.preflight?.ready);'), 'shared preflight presentation must derive authority from backend ready only');
assert(runPreflight.includes('submissionAllowed = preflightReady'), 'auxiliary facts must never independently unlock submission');
assert(runPreflight.includes("headline: '当前无法确认是否可以开始检测'"), 'preflight read failure must stay fail-closed');
assert(runPreflight.includes("headline: '运行前检查未通过，暂不启动检测'"), 'backend not-ready must stay a blocked customer state');
assert(runPreflightSnapshot.includes('只有后端 Preflight 的 <code>ready=true</code> 可以解释为“运行条件已通过”'), 'run snapshot must explain the single readiness authority');

assert(findings.includes("const requestedFindingId = params.get('finding')?.trim() || '';"), 'findings must accept exact finding context');
assert(findings.includes("const requestedVerificationAt = params.get('verification_at')?.trim() || '';"), 'findings must accept exact verification-run context');
assert(findings.includes('setExpandedId(requestedFindingId);'), 'findings must reopen the requested finding');
assert(findings.includes('当前不会用标题相似的问题代替它'), 'stale finding links must not guess replacements');
assert(findings.includes('const regressionEligible = confirmed.some(hasFindingReverificationObligation);'), 'finding revalidation readiness must come from real obligations');
assert(findings.includes('不会提交空验证请求'), 'revalidation must fail closed without real obligations');
assert(findings.includes("verification.state === 'verified_fixed'"), 'findings must separate verified fixes');
assert(findings.includes("verification.state === 'still_failing'"), 'findings must separate continued failures');
assert(findings.includes("verification.state === 'inconclusive'"), 'findings must separate inconclusive validation');
assert(findings.includes("verification.state === 'pending'"), 'findings must separate pending validation');
assert(findings.includes("filter.startsWith('verify:')"), 'validation filters must resolve through the shared finding interpreter');
assert(findings.includes('deriveFindingVerification(right).priority - deriveFindingVerification(left).priority'), 'findings must prioritize unresolved validation risk consistently');
assert(findings.includes("focusGeneratedAt={finding.id === requestedFindingId ? requestedVerificationAt : ''}"), 'findings must focus only the exact requested run on the exact finding');

assert(evidence.includes("const requestedFindingId = params.get('finding')?.trim() || '';"), 'evidence must accept exact finding deep links');
assert(evidence.includes("const requestedVerificationAt = params.get('verification_at')?.trim() || '';"), 'evidence must accept exact verification-run deep links');
assert(evidence.includes("? withEvidence.find((finding) => finding.id === requestedFindingId) || null"), 'evidence must select only the requested finding');
assert(evidence.includes('证据中心不会静默切换到另一条问题来冒充当前证据'), 'missing requested evidence must not fall back silently');
assert(evidence.includes("next.delete('verification_at')"), 'manual evidence selection must drop stale run context');
assert(evidence.includes('<FindingVerificationStatus finding={finding} compact />'), 'evidence list must expose shared verification state');
assert(evidence.includes("focusGeneratedAt={preserveRequestedRun ? requestedVerificationAt : ''}"), 'evidence must focus the exact requested verification run');
assert(evidence.includes('回到这条问题并重新验证'), 'evidence must return to the exact finding for revalidation');
assert(evidenceDrawer.includes('evidenceDeepLinkSearch(finding.id, focusGeneratedAt)'), 'drawer must preserve the exact verification run into full evidence view');
assert(evidencePresentation.includes("params.set('verification_at', normalizedVerificationAt)"), 'deep-link helper must encode exact verification run context');
assert(evidencePresentation.includes("? `${score}/100`\n    : '未评分';"), 'missing evidence score must not become zero');
assert(verificationPanel.includes('前端不会伪造“修复前后 Diff”'), 'missing post-fix evidence must not be fabricated');
assert(verificationPanel.includes('focusGeneratedAt={focusGeneratedAt}'), 'verification panel must preserve exact run focus');

assert(coverage.includes('function finiteNumber(value: unknown): number | null'), 'coverage must preserve missing numerics');
assert(coverage.includes("return parsed == null ? '未上报'"), 'missing coverage must show unreported');
assert(!coverage.includes('Math.max(4, Math.round(clamped * 100))'), 'zero coverage must remain visually zero');
assert(coverage.includes('const canRunRegression = regressionProbeCount > 0;'), 'coverage regression must require real probes');
assert(coverage.includes('disabled={regressionRunning || !canRunRegression}'), 'coverage regression must fail closed');

assert(layout.includes("if (event.key === 'Escape') setMobileNavOpen(false);"), 'mobile navigation must close with Escape');
assert(sidebar.includes('aria-label="主导航"'), 'sidebar must have an accessible name');
assert(topbar.includes('aria-expanded={navOpen} aria-controls="primary-sidebar"'), 'mobile nav toggle must expose controlled state');
assert(responsive.includes('@media (max-width: 560px)'), 'customer actions must have a mobile breakpoint');

assert(packageJson.includes('"test:customer-action-guidance": "node scripts/customer-action-guidance-contract.mjs"'), 'package script missing action guidance contract');
assert(ciGate.includes('"test:customer-action-guidance"'), 'ci gate missing action guidance contract');

console.log('customer action guidance contract passed');
