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

for (const label of ['企业资料理解', '场景与义务生成', '测试数据准备', '真实探针执行', '结果观察与证据收集', '交付门禁与报告']) {
  assert(banner.includes(label), `run lifecycle stage missing: ${label}`);
}
assert(banner.includes('不会用计时器伪造阶段推进'), 'run lifecycle must explicitly reject fake staged progress');
assert(banner.includes('等待服务端回执'), 'running stages must stay pending until a real server response exists');
assert(layout.includes('<RunLifecycleBanner />'), 'layout must surface run lifecycle banner');
assert(packageJson.includes('"test:run-lifecycle": "node scripts/run-lifecycle-contract.mjs"'), 'package script missing run lifecycle contract');
assert(ciGate.includes('"test:run-lifecycle"'), 'ci gate missing run lifecycle contract');

console.log('run lifecycle contract passed');
