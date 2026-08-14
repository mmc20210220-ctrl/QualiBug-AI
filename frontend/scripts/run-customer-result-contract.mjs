import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const summary = read('src/components/run/RunCustomerResultSummary.tsx');
const layout = read('src/components/Layout.tsx');
const runCenterApi = read('src/api/run-center.ts');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(runCenterApi.includes("export const RUN_LIFECYCLE_EVENT = 'qualibug:run-lifecycle';"), 'customer result summary must reuse the real run lifecycle event');
assert(summary.includes("RUN_LIFECYCLE_EVENT, type RunLifecycleDetail"), 'customer result summary must consume the existing lifecycle contract');
assert(summary.includes('if (!next || next.projectId !== project) return;'), 'run summary must ignore events from another customer project');
assert(summary.includes("if (next.phase === 'submitted')"), 'a new run must explicitly clear the previous terminal summary');
assert(summary.includes('setDetail(null);\n  }, [project]);'), 'switching customer projects must clear stale run results');
assert(summary.includes("if (location.pathname !== '/campaigns') setDetail(null);"), 'run result summary must remain scoped to the run center');
assert(summary.includes("title: '本次验证已返回，但覆盖尚未完整'"), 'incomplete execution must have a distinct customer result state');
assert(summary.includes('不能把 0 条发现直接解释为系统没有问题'), 'zero findings under incomplete coverage must not imply safety');
assert(summary.includes('运行回执数量本身不替代正式 Finding 交付口径'), 'raw run counts must not replace customer-ready finding accounting');
assert(summary.includes("primary: { label: '查看未覆盖范围', path: '/coverage' }"), 'incomplete results must lead to coverage remediation');
assert(summary.includes("primary: { label: '查看结果总览', path: '/dashboard' }"), 'terminal results must remain result-first before downstream handling');
assert(summary.includes('最终客户可交付问题数以价值总览和问题清单的正式口径为准'), 'run summary must explain the authoritative customer delivery surfaces');
assert(!summary.includes('detail.coverage'), 'customer result summary must not present lifecycle numeric coverage that may encode missing as zero');

const summaryIndex = layout.indexOf('<RunCustomerResultSummary />');
const lifecycleIndex = layout.indexOf('<RunLifecycleBanner />');
assert(summaryIndex >= 0, 'layout must mount the customer run result summary');
assert(lifecycleIndex >= 0, 'layout must retain the truthful run lifecycle banner');
assert(summaryIndex < lifecycleIndex, 'terminal customer result summary must appear before technical lifecycle detail when both are visible');

assert(packageJson.includes('"test:run-customer-result": "node scripts/run-customer-result-contract.mjs"'), 'package script missing run customer result contract');
assert(ciGate.includes('"test:run-customer-result"'), 'ci gate missing run customer result contract');

console.log('run customer result contract passed');
