import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const dashboard = read('src/pages/Dashboard.tsx');
const runCenter = read('src/pages/EnterpriseCampaigns.tsx');
const findings = read('src/pages/Findings.tsx');
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

assert(packageJson.includes('"test:customer-action-guidance": "node scripts/customer-action-guidance-contract.mjs"'), 'package script missing customer action guidance contract');
assert(ciGate.includes('"test:customer-action-guidance"'), 'ci gate missing customer action guidance contract');

console.log('customer action guidance contract passed');