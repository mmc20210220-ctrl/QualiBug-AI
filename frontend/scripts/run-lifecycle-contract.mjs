import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const runCenter = read('src/api/run-center.ts');
const banner = read('src/components/run/RunLifecycleBanner.tsx');
const layout = read('src/components/Layout.tsx');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

for (const phase of ["phase: 'submitted'", "phase: 'completed'", "phase: 'failed'"]) {
  assert(runCenter.includes(phase), `scan lifecycle event missing: ${phase}`);
}
assert(runCenter.includes("fetch('/api/v1/scan'"), 'run lifecycle must wrap the real scan request');
assert(runCenter.includes('evidenceCount: evidenceCountOf(record)'), 'completed lifecycle must report real evidence count');
assert(runCenter.includes('campaignStatus: textOf(campaign.campaign_status)'), 'completed lifecycle must report campaign status');
assert(runCenter.includes('testDataStatus: textOf(testDataPlan.status)'), 'completed lifecycle must report test data status');

for (const label of ['企业资料理解', '场景与义务生成', '测试数据准备 / 就绪核验', '真实探针执行', '结果观察与证据收集', '交付门禁与报告']) {
  assert(banner.includes(label), `run lifecycle stage missing: ${label}`);
}
assert(banner.includes('liveStatus?.scan_stage_progress?.stages || {}'), 'submitted lifecycle must render server stage telemetry when available');
assert(banner.includes('尚未进入 / 尚未实时上报'), 'a stage must remain pending before its real server boundary is entered');
assert(banner.includes('六个阶段都来自服务端真实执行边界'), 'all six lifecycle stages must be grounded in server boundaries');
assert(banner.includes('发布门禁已经给出 fail/blocked 结论时'), 'release verdict and delivery finalization must stay distinct');
assert(banner.includes('只有最终结果收口后才显示完成'), 'delivery completion must wait for final report/result closure');
assert(banner.includes('任何阶段都不会按计时器或百分比推测'), 'run lifecycle must explicitly reject fake staged progress');
assert(banner.includes("['blocked', 'failed', 'error'].includes(executionStatus)"), 'blocked/failed execution must not render as success');
assert(banner.includes("['plan_only', 'partial', 'partial_coverage', 'coverage_deferred', 'not_executed'].includes(executionStatus)"), 'partial or non-executed results must render as warning');
assert(banner.includes('detail.totalFindings > 0'), 'confirmed findings must not render as clean success');
assert(banner.includes('detail.testDataStatus || \'未报告\''), 'final lifecycle must continue using the real test-data receipt');
assert(banner.includes('detail.executionStatus || \'未报告\''), 'final lifecycle must continue using the real execution receipt');
assert(layout.includes('<RunLifecycleBanner />'), 'layout must surface run lifecycle banner');
assert(packageJson.includes('"test:run-lifecycle": "node scripts/run-lifecycle-contract.mjs"'), 'package script missing run lifecycle contract');
assert(ciGate.includes('"test:run-lifecycle"'), 'ci gate missing run lifecycle contract');

console.log('run lifecycle contract passed');
