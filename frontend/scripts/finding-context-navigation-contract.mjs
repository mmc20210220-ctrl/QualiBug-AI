import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const dashboard = read('src/pages/Dashboard.tsx');
const dashboardFocus = read('src/components/dashboard/DashboardFocusFindingCard.tsx');
const regressionClosure = read('src/components/dashboard/RegressionClosurePanel.tsx');
const findings = read('src/pages/Findings.tsx');
const evidence = read('src/pages/EvidenceChain.tsx');
const releaseGate = read('src/pages/ReleaseGate.tsx');
const evidencePresentation = read('src/lib/evidence-presentation.ts');
const projectNavigation = read('src/lib/project-navigation.ts');
const responsive = read('src/styles/customer-responsive.css');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(evidencePresentation.includes("params.set('finding', normalized);"), 'finding deep-link helper must encode a stable finding identity');
assert(evidencePresentation.includes("params.set('verification_at', normalizedVerificationAt)"), 'finding deep-link helper must preserve an exact verification run when supplied');
assert(projectNavigation.includes("const navigateToProjectPath = useCallback((pathname: string, projectId?: string, currentSearch = '') =>"), 'project navigation must accept entity query context');
assert(projectNavigation.includes('buildProjectPath(pathname, projectId, currentSearch)'), 'project navigation must merge project with the supplied finding context');

assert(dashboard.includes('hasFindingReverificationObligation'), 'dashboard must reuse the shared real re-verification obligation helper');
assert(dashboard.includes('<DashboardFocusFindingCard key={finding.id} finding={finding} project={project} />'), 'dashboard must delegate exact finding navigation to the focus component');
assert(dashboardFocus.includes("navigateToProjectPath('/findings', project, evidenceDeepLinkSearch(finding.id))"), 'dashboard focus card must open the exact finding instead of the generic list');
assert(dashboardFocus.includes('查看这条验证'), 'dashboard focus card must expose an exact validation action');
assert(!dashboardFocus.includes('处理这条问题'), 'dashboard focus must not frame finding navigation as enterprise task handling');
assert(dashboardFocus.includes("(finding.evidence_chain?.length || 0) > 0"), 'dashboard focus must only expose exact evidence navigation when the finding has a real evidence package');
assert(dashboardFocus.includes("navigateToProjectPath('/evidence', project, evidenceDeepLinkSearch(finding.id))"), 'dashboard focus evidence action must keep the exact finding identity');
assert(dashboard.includes('const regressionEligible = findings.some(hasFindingReverificationObligation);'), 'dashboard validation readiness must come from real included-in-suite findings');
assert(dashboard.includes('const hasRegressionObligation = regressionFindings.some(hasFindingReverificationObligation);'), 'dashboard validation handler must recompute the real obligation before calling the API');
assert(dashboard.includes('不会提交空验证请求'), 'dashboard must explain why an empty validation is rejected');
assert(dashboard.includes("regressionEligible ? '修复后验证' : '暂无可执行验证'"), 'dashboard top validation action must use product-owned verification wording');

assert(regressionClosure.includes('regressionEligible: boolean;'), 'validation closure must receive explicit real-obligation readiness');
assert(regressionClosure.includes("regressionEligible ? '执行 Release 验证' : '暂无可执行验证'"), 'release validation action must explain the fail-closed state');
assert(regressionClosure.includes("regressionEligible ? '执行 Smoke 验证' : '暂无可执行验证'"), 'smoke validation action must explain the fail-closed state');

assert(findings.includes("const requestedFindingId = params.get('finding')?.trim() || '';"), 'findings must read the exact finding identity');
assert(findings.includes("const requestedVerificationAt = params.get('verification_at')?.trim() || '';"), 'findings must read the exact verification run identity');
assert(findings.includes('setExpandedId(requestedFindingId);'), 'findings must reopen the requested finding on round trip');
assert(findings.includes('当前不会用标题相似的问题代替它'), 'stale finding context must never fall back by title');
assert(findings.includes("focusGeneratedAt={finding.id === requestedFindingId ? requestedVerificationAt : ''}"), 'findings must focus the requested run only on the requested finding');
assert(findings.includes("value: 'verify:pending'"), 'findings must expose waiting-validation filtering');
assert(findings.includes("value: 'verify:still_failing'"), 'findings must expose still-failing filtering');
assert(findings.includes("value: 'verify:inconclusive'"), 'findings must expose inconclusive-validation filtering');
assert(findings.includes("value: 'verify:verified_fixed'"), 'findings must expose verified-fixed filtering');

assert(evidence.includes('const findingContextSearch = evidenceDeepLinkSearch('), 'evidence must retain selected finding and optional verification run context');
assert(evidence.includes("next.delete('verification_at')"), 'manual evidence selection must clear stale verification-run context');
assert(evidence.includes("navigateToProjectPath('/release', project, findingContextSearch)"), 'evidence to release navigation must preserve the exact finding and run context');
assert(evidence.includes('证据中心不会静默切换到另一条问题来冒充当前证据'), 'evidence must never silently swap a requested finding');

assert(releaseGate.includes("const requestedFindingId = params.get('finding')?.trim() || '';"), 'release review must accept a finding context');
assert(releaseGate.includes("const requestedVerificationAt = params.get('verification_at')?.trim() || '';"), 'release review must accept an exact verification-run context');
assert(releaseGate.includes('customerFindings.find((finding) => finding.id === requestedFindingId) || null'), 'release review must resolve the exact finding by id only');
assert(releaseGate.includes('focusGeneratedAt={requestedVerificationAt}'), 'release review must retain exact run focus');
assert(releaseGate.includes('发布门禁仍按整个项目的真实 Gate 判定'), 'single finding context must not replace project-level release authority');
assert(releaseGate.includes('发布页不会按标题猜测替代问题'), 'stale release finding context must not guess a replacement');
assert(releaseGate.includes("navigateToProjectPath('/findings', project, findingContextSearch)"), 'release review must return to the exact finding');
assert(releaseGate.includes("navigateToProjectPath('/evidence', project, findingContextSearch)"), 'release review must return to the exact evidence when it exists');
assert(releaseGate.includes('deriveReleasePresentation({'), 'release decision must remain driven by the existing project-level release presentation authority');

assert(responsive.includes('.focus-card .settings-actions {'), 'dashboard focus actions must wrap on narrow layouts');
assert(responsive.includes('.focus-card .settings-actions .btn,'), 'dashboard focus buttons must become touch-friendly at the mobile breakpoint');
assert(packageJson.includes('"test:finding-context-navigation": "node scripts/finding-context-navigation-contract.mjs"'), 'package script missing finding context navigation contract');
assert(ciGate.includes('"test:finding-context-navigation"'), 'ci gate missing finding context navigation contract');

console.log('finding context navigation contract passed');
