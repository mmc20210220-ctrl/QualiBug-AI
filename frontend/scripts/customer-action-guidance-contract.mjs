import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const app = read('src/App.tsx');
const dashboard = read('src/pages/Dashboard.tsx');
const releasePresentation = read('src/lib/release-presentation.ts');
const releaseGate = read('src/pages/ReleaseGate.tsx');
const runCenter = read('src/pages/EnterpriseCampaigns.tsx');
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

assert(dashboard.includes("const coverageDeferred = campaignStatus === 'coverage_deferred';"), 'dashboard must distinguish incomplete coverage');
assert(dashboard.includes('const resultIncomplete = pipelineUnhealthy || campaignBlocked || coverageDeferred;'), 'dashboard must have one incomplete-result authority');
assert(dashboard.includes('当前 0 个 P0 只代表已覆盖部分，不能直接推导为安全'), 'partial coverage must never imply safety');
assert(dashboard.includes('下一步：{nextAction.title}'), 'dashboard must expose one state-driven next action');
assert(dashboard.includes("import { hasFindingReverificationObligation } from '../lib/finding-verification';"), 'dashboard must reuse the shared re-verification obligation helper');
assert(dashboard.includes('const regressionEligible = findings.some(hasFindingReverificationObligation);'), 'dashboard validation readiness must come from real obligations');
assert(dashboard.includes('const hasRegressionObligation = regressionFindings.some(hasFindingReverificationObligation);'), 'dashboard validation handler must recompute current obligations');
assert(dashboard.includes('if (!hasRegressionObligation) {'), 'dashboard validation handler must fail closed');
assert(dashboard.includes('不会提交空验证请求'), 'dashboard must explain empty validation rejection');
assert(dashboard.includes("? { title: '查看已确认问题与验证状态', label: '查看验证', path: '/findings' }"), 'dashboard confirmed findings must lead into the validation surface');
assert(dashboard.includes('查看这条验证'), 'dashboard exact finding action must use validation language');
assert(!dashboard.includes('处理这条问题'), 'dashboard must not frame a finding as an enterprise task');

assert(releasePresentation.includes('export function deriveReleasePresentation'), 'release truth must use one shared interpreter');
assert(releasePresentation.includes("gateOverall === 'pass'"), 'green release state must require explicit pass');
assert(releasePresentation.includes('尚未取得完整发布门禁回执'), 'missing gate data must not imply safety');
assert(releaseGate.includes('deriveReleasePresentation({'), 'release page must remain project-gate driven');
assert(releaseGate.includes('当前不能把 0 条门禁数据解释为“可以发布”'), 'zero gate rows must not imply release');
assert(releaseGate.includes('单条 Finding 的修复后验证状态只是发布依据之一，不会覆盖项目级门禁'), 'single finding validation must not replace release authority');
assert(releaseGate.includes('仅凭“列表中消失”不能断言已修复'), 'missing finding must not be inferred as fixed');

assert(runCenter.includes('const runBlockedByPreflight = !loadingPreflight && !preflightReady;'), 'run center must model preflight blocking');
assert(runCenter.includes('disabled={runDisabled}'), 'run action must use real readiness');
assert(runCenter.includes('运行前检查未通过时不会提交扫描请求'), 'run center must explain fail-closed readiness');

assert(findings.includes("const requestedFindingId = params.get('finding')?.trim() || '';"), 'findings must accept exact finding context');
assert(findings.includes('setExpandedId(requestedFindingId);'), 'findings must reopen the requested finding');
assert(findings.includes('当前不会用标题相似的问题代替它'), 'stale finding links must not guess replacements');
assert(findings.includes("const hasActiveFilter = filter !== 'all' || Boolean(searchQuery.trim());"), 'findings must distinguish filtered-empty from true-empty');
assert(findings.includes('不要把空列表直接解释为系统没有问题'), 'true empty findings must preserve coverage boundary');
assert(findings.includes('const regressionEligible = confirmed.some(hasFindingReverificationObligation);'), 'finding revalidation readiness must come from real obligations');
assert(findings.includes('const currentEligible = confirmed.some(hasFindingReverificationObligation);'), 'revalidation handler must recompute current real obligations');
assert(findings.includes('不会提交空验证请求'), 'revalidation must fail closed without real obligations');
assert(findings.includes("regressionEligible ? '修复后重新验证' : '暂无可执行验证'"), 'findings must use validation-first customer wording');
assert(findings.includes("verification.state === 'verified_fixed'"), 'findings must separate verified fixes');
assert(findings.includes("verification.state === 'still_failing'"), 'findings must separate continued failures');
assert(findings.includes("verification.state === 'inconclusive'"), 'findings must separate inconclusive validation');
assert(findings.includes("verification.state === 'pending'"), 'findings must separate pending validation');
assert(findings.includes("{ label: `等待验证 (${pendingRegression.length})`, value: 'verify:pending' }"), 'findings must expose a waiting-validation filter');
assert(findings.includes("{ label: `仍失败 (${failedRegression.length})`, value: 'verify:still_failing' }"), 'findings must expose a still-failing filter');
assert(findings.includes("{ label: `无法确认 (${inconclusiveRegression.length})`, value: 'verify:inconclusive' }"), 'findings must expose an inconclusive filter');
assert(findings.includes("{ label: `验证通过 (${passedRegression.length})`, value: 'verify:verified_fixed' }"), 'findings must expose a verified-fixed filter');
assert(findings.includes("filter.startsWith('verify:')"), 'validation filters must resolve through the shared finding interpreter');

assert(evidence.includes("const requestedFindingId = params.get('finding')?.trim() || '';"), 'evidence must accept exact finding deep links');
assert(evidence.includes("? withEvidence.find((finding) => finding.id === requestedFindingId) || null"), 'evidence must select only the requested finding');
assert(evidence.includes('证据中心不会静默切换到另一条问题来冒充当前证据'), 'missing requested evidence must not fall back silently');
assert(evidence.includes('setParams(next, { replace: true });'), 'evidence selection must stay in URL state');
assert(evidence.includes('evidenceScoreLabel(finding)'), 'evidence list must preserve unknown scores');
assert(evidence.includes('<FindingVerificationPanel finding={selected} />'), 'evidence must show repair verification comparison');
assert(evidence.includes('回到这条问题并重新验证'), 'evidence must return to the exact finding for revalidation');
assert(evidenceDrawer.includes('证据中心完整查看'), 'drawer must expose full exact evidence navigation');
assert(evidencePresentation.includes("? `${score}/100`\n    : '未评分';"), 'missing evidence score must not become zero');
assert(verificationPanel.includes('前端不会伪造“修复前后 Diff”'), 'missing post-fix evidence must not be fabricated');

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
