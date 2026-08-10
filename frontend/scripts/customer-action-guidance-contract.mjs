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

const releaseDecisionStart = dashboardUtils.indexOf('export function releaseDecision');
const releaseDecisionEnd = dashboardUtils.indexOf('\n}\n\n// ─── Campaign helpers', releaseDecisionStart);
assert(releaseDecisionStart >= 0 && releaseDecisionEnd > releaseDecisionStart, 'release decision helper must exist');
const releaseDecisionBody = dashboardUtils.slice(releaseDecisionStart, releaseDecisionEnd);
assert(releaseDecisionBody.indexOf('if (p0 > 0)') < releaseDecisionBody.indexOf('if (unhealthy || blocked)'), 'confirmed P0 must outrank incomplete coverage or unhealthy scan status in release advice');

assert(releasePresentation.includes('export function deriveReleasePresentation'), 'release presentation must have one frontend priority interpreter');
assert(releasePresentation.indexOf('if (p0Count > 0)') < releasePresentation.indexOf('if (hasIndependentGateFailure)'), 'known P0 must stay ahead of other release presentation states');
assert(releasePresentation.includes('gateFailureOnlyExplainsIncomplete'), 'campaign-only incomplete coverage must be distinguished from an independent gate failure');
assert(releasePresentation.includes("campaignStatus === 'coverage_deferred'"), 'release presentation must treat deferred coverage as incomplete');
assert(releasePresentation.includes("gateOverall === 'pass'"), 'green release presentation must require an explicit pass branch');
assert(releasePresentation.includes('尚未取得完整发布门禁回执'), 'missing gate data must not be presented as safe');

assert(releaseGate.includes('deriveReleasePresentation({'), 'release page must use the shared frontend release priority interpreter');
assert(releaseGate.includes("? { label: '处理 P0 问题', path: '/findings' }"), 'release page must route confirmed P0 to issue handling');
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

assert(packageJson.includes('"test:customer-action-guidance": "node scripts/customer-action-guidance-contract.mjs"'), 'package script missing customer action guidance contract');
assert(ciGate.includes('"test:customer-action-guidance"'), 'ci gate missing customer action guidance contract');

console.log('customer action guidance contract passed');