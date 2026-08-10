import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const release = read('src/pages/ReleaseGate.tsx');
const snapshot = read('src/components/release/ReleaseDecisionSnapshot.tsx');
const styles = read('src/components/release/ReleaseDecisionSnapshot.css');
const releasePresentation = read('src/lib/release-presentation.ts');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(!release.includes('useReleaseData'), 'release page must not use frontend-synthesized release checks as project-gate authority');
assert(release.includes('const backendGate = asRecord(pipelineRecord.release_gate);'), 'release page must read the real backend release_gate directly');
assert(release.includes('asArray(backendGate.checks).map(releaseCheckFrom)'), 'release checklist must come from real backend gate checks');
assert(release.includes("const overall = ['pass', 'fail', 'pending'].includes(explicitOverall) ? explicitOverall : '';"), 'release overall must require an explicit backend gate status');
assert(release.includes('const hasGateData = Boolean(overall) || checks.length > 0;'), 'release page must distinguish missing gate data from an empty successful gate');
assert(release.includes('gateOverall: overall'), 'shared release presentation must receive only the real backend overall gate');
assert(release.includes('gateChecks: checks'), 'shared release presentation must receive only real backend checks');
assert(!release.includes('localChecks'), 'release page must never synthesize local pass/fail gate checks');
for (const fakeGate of ['P0 缺陷阻塞', '认证授权检测', '数据完整性校验', 'DB 验证']) {
  assert(!release.includes(fakeGate), `release page must not synthesize a local gate: ${fakeGate}`);
}

assert(release.includes("value: `首个上报失败：${failChecks[0].name}`"), 'first-screen blocker must expose the first real backend failed gate without inventing severity');
assert(release.includes("value: `首个待处理：${pendingChecks[0].name}`"), 'first-screen pending state must expose a real backend pending gate');
assert(release.includes('后端已明确返回 fail，但当前没有提供可展示的失败检查项；前端不会猜测具体根因'), 'overall fail without a concrete check must stay truthful');
assert(release.includes('没有明确 overall=pass 时前端不会放行'), 'all-pass checks without explicit overall pass must not become green');
assert(release.includes('当前没有真实项目级 release_gate 回执；0 个问题不能替代 Gate'), 'missing gate must stay fail-closed');

assert(release.includes("text(asRecord(regressionCampaign.ci_feedback).gate_status)"), 'release page must consume the persisted regression gate when present');
assert(release.includes('text(regressionRun.gate_status)'), 'release page must preserve the direct regression-run gate shape');
assert(release.includes('text(latestRegressionRun.gate_status)'), 'release page must preserve the regression-summary latest-run shape');
assert(release.includes('该失败状态直接参与当前发布结论'), 'failed regression must explain its real impact on current release presentation');
assert(release.includes('单次回归通过不等于项目级 Gate 放行'), 'passed regression must not independently release the project');
assert(release.includes('没有前一版发布快照时，前端也不会声称“发布结论已被改变”'), 'release page must not fabricate a before/after release transition without a real prior snapshot');

for (const label of ['项目级发布结论', '真实项目级 Gate', '最新修复后回归', '现在最应该做']) {
  assert(snapshot.includes(label), `release decision snapshot missing customer-first label: ${label}`);
}
assert(snapshot.includes('绿色只来自共享 Release Presentation 对真实项目级 Gate 的明确通过结论'), 'release snapshot must state the green-release authority');
assert(snapshot.includes('单条 Finding 与单次回归都不能独立覆盖项目级发布门禁'), 'release snapshot must preserve project-level authority');
assert(release.includes('<ReleaseDecisionSnapshot'), 'release page must render decision summary before audit details');
assert(release.indexOf('<ReleaseDecisionSnapshot') < release.indexOf('id="release-gate-checklist"'), 'decision summary must appear before the full gate checklist');
assert(release.includes('<details className="release-checklist" id="release-gate-checklist"'), 'full backend gate checklist must remain available as secondary evidence');
assert(release.includes('<details className="card mb-4 dashboard-more-actions">'), 'secondary release actions must remain below the primary next action');
assert(release.includes('单条 Finding 的修复后验证状态只是发布依据之一，不会覆盖项目级门禁'), 'finding review context must not replace project release authority');
assert(release.includes('仅凭“列表中消失”不能断言已修复'), 'a finding disappearing from the current list must not imply fixed');

assert(releasePresentation.includes("if (gateOverall === 'pass' && input.hasGateData !== false)"), 'shared release authority must require explicit backend pass before green');
assert(releasePresentation.includes('不能仅凭 0 个已确认问题推导为可以发布'), 'shared release authority must reject zero-finding safety inference');

assert(styles.includes('grid-template-columns: repeat(2, minmax(0, 1fr));'), 'release first screen must keep gate and regression facts readable on desktop');
assert(styles.includes('@media (max-width: 760px)'), 'release first screen must have a mobile breakpoint');
assert(styles.includes('grid-template-columns: minmax(0, 1fr);'), 'release decision facts must stack on narrow screens');

assert(packageJson.includes('"test:release-decision-first": "node scripts/release-decision-first-contract.mjs"'), 'package script missing release decision-first contract');
assert(ciGate.includes('"test:release-decision-first"'), 'ci gate missing release decision-first contract');

console.log('release decision-first contract passed');
