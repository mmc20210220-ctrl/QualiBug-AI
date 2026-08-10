import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const dashboard = read('src/pages/Dashboard.tsx');
const dashboardUtils = read('src/lib/dashboard-utils.ts');
const releasePresentation = read('src/lib/release-presentation.ts');
const releaseGate = read('src/pages/ReleaseGate.tsx');
const runCenter = read('src/pages/EnterpriseCampaigns.tsx');
const findings = read('src/pages/Findings.tsx');
const evidence = read('src/pages/EvidenceChain.tsx');
const coverage = read('src/pages/CoverageMatrix.tsx');
const layout = read('src/components/Layout.tsx');
const sidebar = read('src/components/Sidebar.tsx');
const topbar = read('src/components/Topbar.tsx');
const main = read('src/main.tsx');
const responsive = read('src/styles/customer-responsive.css');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(dashboard.includes("const coverageDeferred = campaignStatus === 'coverage_deferred';"), 'dashboard must distinguish deferred coverage from a clean completed scan');
assert(dashboard.includes('const resultIncomplete = pipelineUnhealthy || campaignBlocked || coverageDeferred;'), 'dashboard must use one incomplete-result authority for customer actions');
assert(dashboard.includes('campaignBlocked || coverageDeferred'), 'coverage-deferred results must not receive the clean release decision');
assert(dashboard.includes("? { title: '继续覆盖剩余范围', label: '继续检测', path: '/campaigns' }"), 'deferred coverage must lead to continued detection');
assert(dashboard.includes('当前 0 个 P0 只代表已覆盖部分，不能直接推导为安全'), 'dashboard must not translate zero P0 under partial coverage into safety');
assert(dashboard.includes('下一步：{nextAction.title}'), 'dashboard must surface a state-driven next action');
assert(dashboard.includes("resultIncomplete && <button className=\"btn btn-secondary\" onClick={() => navigateToProjectPath('/coverage', project)}>查看未覆盖范围</button>"), 'incomplete results must expose coverage navigation');
assert(dashboard.includes("{currentScanDefects > 0 && ("), 'regression action should only be primary follow-up when confirmed defects exist');
assert(dashboard.includes("resultIncomplete ? '导出当前报告' : '导出报告'"), 'incomplete result export must be labeled as a current report, not a final-safe report');
assert(dashboard.includes('const releaseGate = asRecord(record.release_gate);'), 'dashboard hero must consume the same backend release gate surfaced by the banner');
assert(dashboard.includes('const releaseGateChecks = (Array.isArray(releaseGate.checks) ? releaseGate.checks : []).map'), 'dashboard must project release gate checks into the shared presentation interpreter');
assert(dashboard.includes('const releaseGateOverall = asText(releaseGate.overall_status || releaseGate.verdict || releaseGate.status);'), 'dashboard must project the release gate terminal status');
assert(dashboard.includes('const hasReleaseGateData = Object.keys(releaseGate).length > 0;'), 'dashboard must distinguish missing release gate data from an explicit result');
assert(dashboard.includes('const regressionCampaign = Object.keys(asRecord(record.regression_campaign)).length > 0'), 'dashboard must consume the same regression campaign family used by the gate banner');
assert(dashboard.includes('const regressionGateStatus = asText(asRecord(regressionCampaign.ci_feedback).gate_status).toLowerCase();'), 'dashboard must project the latest regression gate status');
assert(dashboard.includes('const regressionFailed = regressionGateStatus === \'failed\';'), 'dashboard must explicitly recognize a known regression failure');
assert(dashboard.includes('releaseGateOverall,\n    releaseGateChecks,\n    hasReleaseGateData,\n    regressionGateStatus,'), 'dashboard hero decision must pass release and regression gate facts into the shared presentation priority');
assert(dashboard.includes("? { title: '先处理回归失败，再考虑发布', label: '查看发布门禁', path: '/release' }"), 'dashboard must route a regression failure back to the release decision flow');

const releaseDecisionStart = dashboardUtils.indexOf('export function releaseDecision');
const releaseDecisionEnd = dashboardUtils.indexOf('\n}\n\n// ─── Campaign helpers', releaseDecisionStart);
assert(releaseDecisionStart >= 0 && releaseDecisionEnd > releaseDecisionStart, 'release decision helper must exist');
const releaseDecisionBody = dashboardUtils.slice(releaseDecisionStart, releaseDecisionEnd);
assert(dashboardUtils.includes("import { deriveReleasePresentation, type ReleasePresentationCheck } from './release-presentation';"), 'dashboard release decision must reuse the shared release presentation priority');
assert(releaseDecisionBody.includes('const prioritized = deriveReleasePresentation({'), 'dashboard high-risk release states must flow through the shared interpreter');
assert(releaseDecisionBody.includes('gateOverall,'), 'dashboard release helper must accept the real release gate status');
assert(releaseDecisionBody.includes('gateChecks,'), 'dashboard release helper must accept the real release gate checks');
assert(releaseDecisionBody.includes('regressionGateStatus,'), 'dashboard release helper must accept the latest regression gate status');
assert(releaseDecisionBody.includes('if (hasGateData || regressionGateStatus) return prioritized;'), 'explicit release or regression gate data must override the dashboard fallback summary');
assert(releaseDecisionBody.indexOf('if (p0 > 0)') < releaseDecisionBody.indexOf('if (unhealthy || blocked)'), 'confirmed P0 must outrank incomplete coverage or unhealthy scan status in release advice');

assert(releasePresentation.includes('export function deriveReleasePresentation'), 'release presentation must have one frontend priority interpreter');
assert(releasePresentation.indexOf('if (p0Count > 0)') < releasePresentation.indexOf('if (regressionFailed)'), 'known P0 must stay ahead of regression state');
assert(releasePresentation.indexOf('if (regressionFailed)') < releasePresentation.indexOf('if (hasIndependentGateFailure)'), 'known regression failure must be treated as an explicit blocker before incomplete-state fallbacks');
assert(releasePresentation.includes("const regressionFailed = regressionGateStatus === 'failed';"), 'release presentation must recognize regression failure');
assert(releasePresentation.includes("['pending', 'not_ready', 'manual_approval_required'].includes(regressionGateStatus)"), 'release presentation must recognize pending regression closure');
assert(releasePresentation.includes('gateFailureOnlyExplainsIncomplete'), 'campaign-only incomplete coverage must be distinguished from an independent gate failure');
assert(releasePresentation.includes("campaignStatus === 'coverage_deferred'"), 'release presentation must treat deferred coverage as incomplete');
assert(releasePresentation.includes("gateOverall === 'pass'"), 'green release presentation must require an explicit pass branch');
assert(releasePresentation.includes('最新回归状态'), 'green release copy must preserve the regression boundary');
assert(releasePresentation.includes('尚未取得完整发布门禁回执'), 'missing gate data must not be presented as safe');

assert(releaseGate.includes('deriveReleasePresentation({'), 'release page must use the shared frontend release priority interpreter');
assert(releaseGate.includes('regressionGateStatus,'), 'release page must pass the latest regression gate into the shared interpreter');
assert(releaseGate.includes("? '不建议发布：最新回归门禁失败'"), 'release page must name a known regression failure instead of showing a generic gate state');
assert(releaseGate.includes("? { label: '处理 P0 问题', path: '/findings' }"), 'release page must route confirmed P0 to issue handling');
assert(releaseGate.includes("? { label: '处理回归失败', path: '/findings' }"), 'release page must route regression failure to issue handling when current findings exist');
assert(releaseGate.includes("? { label: '继续检测剩余范围', path: '/campaigns' }"), 'release page must route deferred coverage back to detection');
assert(releaseGate.includes('当前不能把 0 条门禁数据解释为“可以发布”'), 'missing release checks must not imply release safety');
assert(releaseGate.includes("releasePresentation.incomplete && <button className=\"btn btn-secondary\" onClick={() => navigateToProjectPath('/coverage', project)}>查看未覆盖范围</button>"), 'release page must expose coverage when the result is incomplete');
assert(releaseGate.includes('发布依据暂时不可用'), 'release page must distinguish data read failure from a release decision');

assert(runCenter.includes('const runBlockedByPreflight = !loadingPreflight && !preflightReady;'), 'run center must model preflight blocking explicitly');
assert(runCenter.includes('const runDisabled = running || loadingPreflight || loadingFixtures || runBlockedByPreflight || runBlockedByScenario;'), 'run button must be disabled by real readiness blockers');
assert(runCenter.includes('if (!preflightReady) {'), 'run handler must fail closed if invoked without a passed preflight');
assert(runCenter.includes('disabled={runDisabled}'), 'run button must use the unified readiness condition');
assert(runCenter.includes("? blockers.length > 0 ? `先处理 ${blockers.length} 项阻断` : '运行前检查未通过'"), 'run button must explain why it cannot start');
assert(runCenter.includes('const tone = resultTone(response);'), 'run completion toast must reuse the same result tone authority as the result card');
assert(runCenter.includes("completed: '已完成真实验证'"), 'run center must translate completed execution status for customers');
assert(runCenter.includes('运行前检查未通过时不会提交扫描请求'), 'run center must explain fail-closed preflight behavior');

assert(findings.includes("const hasActiveFilter = filter !== 'all' || Boolean(searchQuery.trim());"), 'findings must distinguish an empty dataset from an empty filter result');
assert(findings.includes('{(loading || confirmed.length > 0) && ('), 'zero-finding state must not render a meaningless all-zero filter bar');
assert(findings.includes('const clearFilters = (): void => {'), 'filtered-empty state must provide a direct reset action');
assert(findings.includes("onClick={() => navigateToProjectPath('/campaigns', project)}>继续检测</button>"), 'true zero-finding state must offer continued detection');
assert(findings.includes("onClick={() => navigateToProjectPath('/coverage', project)}>查看覆盖范围</button>"), 'true zero-finding state must expose coverage before implying safety');
assert(findings.includes('不要把空列表直接解释为系统没有问题'), 'zero-finding copy must preserve the empty-result safety boundary');

assert(evidence.includes('const selected = withEvidence.find((f) => f.id === selectedId) || withEvidence[0] || null;'), 'evidence center should open the first real evidence package by default');
assert(evidence.includes('!loading && error && ('), 'evidence read failures must have a dedicated error state');
assert(evidence.includes('读取失败不能解释为“没有证据”或“没有问题”'), 'evidence read failure must never collapse into an empty-safe state');
assert(evidence.includes('customerFindings.length === 0'), 'evidence center must distinguish zero findings from missing evidence packages');
assert(evidence.includes('customerFindings.length > 0 && withEvidence.length === 0'), 'confirmed findings without evidence packages must be a distinct state');
assert(evidence.includes('个已确认问题当前没有可展示证据包'), 'missing evidence for confirmed findings must be visible and actionable');
assert(evidence.includes("onClick={() => navigateToProjectPath('/coverage', project)}>查看覆盖范围</button>"), 'zero-finding evidence state must expose coverage');

assert(coverage.includes('function finiteNumber(value: unknown): number | null'), 'coverage must preserve missing numeric values instead of coercing them to zero');
assert(coverage.includes("return parsed == null ? '未上报'"), 'missing coverage rates must be shown as unreported, not 0%');
assert(!coverage.includes('Math.max(4, Math.round(clamped * 100))'), 'zero coverage must not be painted as a synthetic non-zero bar');
assert(coverage.includes('width: `${percent}%`'), 'coverage bar width must reflect the real percentage exactly');
assert(coverage.includes("aria-label={parsed == null ? '覆盖率未上报' : `覆盖率 ${percent}%`}"), 'coverage progress must expose truthful accessible semantics');
assert(coverage.includes('const canRunRegression = regressionProbeCount > 0;'), 'coverage must derive regression readiness from real persisted probes');
assert(coverage.includes("toast.show('当前没有可执行的回归义务；先形成回归探针后再运行回归。', 'warning');"), 'coverage regression handler must fail closed without real probes');
assert(coverage.includes('disabled={regressionRunning || !canRunRegression}'), 'coverage regression controls must disable without real obligations');
assert(coverage.includes("? { title: `继续关闭 ${gaps.length} 个风险家族缺口`, label: '继续检测剩余范围', path: '/campaigns' }"), 'coverage gaps must produce a direct continued-detection action');
assert(coverage.includes("onClick={() => navigateToProjectPath('/campaigns', project)}>启动标准扫描</button>"), 'empty coverage state must let the customer start the scan');
assert(coverage.includes("onClick={() => navigateToProjectPath('/settings', project)}>检查接入条件</button>"), 'empty coverage state must expose setup remediation');
assert(coverage.includes('下一步：{nextAction.title}'), 'coverage must surface one state-driven next action');

assert(layout.includes("if (event.key === 'Escape') setMobileNavOpen(false);"), 'mobile navigation must close with Escape');
assert(sidebar.includes('id="primary-sidebar"'), 'sidebar must expose a stable control target');
assert(sidebar.includes('aria-label="主导航"'), 'sidebar landmark must be named');
assert(sidebar.includes('aria-label="客户项目导航"'), 'project navigation landmark must be named');
assert(sidebar.includes('aria-hidden="true"'), 'sidebar backdrop must stay out of the accessibility tree');
assert(topbar.includes("'/jobs': '后台任务'"), 'topbar must label the background jobs page correctly');
assert(topbar.includes('aria-expanded={navOpen} aria-controls="primary-sidebar"'), 'mobile nav toggle must expose expanded state and controlled sidebar');
assert(topbar.includes("if (event.key === 'Escape') setShowTenantMenu(false);"), 'customer switcher must close with Escape');
assert(topbar.includes('aria-controls="tenant-switcher-menu"'), 'customer switcher trigger must reference its menu');
assert(topbar.includes('id="tenant-switcher-menu"'), 'customer switcher menu must expose a stable control target');

assert(main.indexOf("import './index.css';") < main.indexOf("import './styles/customer-responsive.css';"), 'customer responsive overrides must load after legacy index styles');
assert(responsive.includes('.evidence-layout {'), 'responsive layer must target the actual evidence page class');
assert(responsive.includes('@media (max-width: 1024px)'), 'evidence split must have a tablet breakpoint');
assert(responsive.includes('grid-template-columns: 1fr;'), 'narrow evidence layout must collapse to one column');
assert(responsive.includes('@media (max-width: 720px)'), 'customer page headers must have a narrow breakpoint');
assert(responsive.includes('.findings-page-head {'), 'findings/evidence page header must stack safely on narrow screens');
assert(responsive.includes('@media (max-width: 560px)'), 'customer actions must have a mobile breakpoint');
assert(responsive.includes('.action-bar .btn,'), 'mobile action bar buttons must be explicitly handled');
assert(responsive.includes('width: 100%;'), 'mobile primary actions must expand to touch-friendly full width');

assert(packageJson.includes('"test:customer-action-guidance": "node scripts/customer-action-guidance-contract.mjs"'), 'package script missing customer action guidance contract');
assert(ciGate.includes('"test:customer-action-guidance"'), 'ci gate missing customer action guidance contract');

console.log('customer action guidance contract passed');