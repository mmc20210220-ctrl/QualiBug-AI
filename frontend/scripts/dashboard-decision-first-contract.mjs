import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');
const assert = (condition, message) => { if (!condition) throw new Error(message); };

const dashboard = read('src/pages/Dashboard.tsx');
const hero = read('src/components/dashboard/ValueHero.tsx');
const heroStyles = read('src/components/dashboard/ValueHero.css');
const dashboardUtils = read('src/lib/dashboard-utils.ts');
const releasePresentation = read('src/lib/release-presentation.ts');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

assert(dashboardUtils.includes('return deriveReleasePresentation({'), 'dashboard release decision must delegate to the shared release presentation authority');
assert(!dashboardUtils.includes("return { color: 'green', label: '可以发布', advice: '当前未发现阻断性问题，可正常推进发布' }"), 'dashboard must not restore a zero-finding green release fallback');
assert(releasePresentation.includes('尚未取得完整发布门禁回执，不能仅凭 0 个已确认问题推导为可以发布'), 'shared release authority must remain fail-closed when gate data is missing');
assert(dashboardUtils.includes('客户修复后由 QualiBug 重新验证'), 'dashboard headline must remain inside the product-owned validation loop');
assert(!dashboardUtils.includes('可直接进入整改'), 'dashboard must not present customer internal remediation workflow as a QualiBug-owned state');

assert(dashboard.includes("const level = decision.color === 'red' ? 'blocked' : decision.color === 'yellow' ? 'attention' : 'safe';"), 'dashboard risk ring must follow the shared release decision color');
assert(dashboard.includes("decision.color === 'green'\n                  ? '当前未发现阻断性问题'\n                  : '当前无已确认阻断问题，发布结论待确认'"), 'zero confirmed blockers must not become a positive release conclusion without a green gate decision');
assert(dashboard.includes('const highestPriorityFinding = focusFindings[0] || null;'), 'dashboard must identify one highest-priority confirmed finding for the first screen');
assert(dashboard.includes('deriveFindingVerification(right).priority - deriveFindingVerification(left).priority'), 'highest-priority finding must reuse the shared verification risk ordering');
assert(dashboard.includes("evidenceDeepLinkSearch(highestPriorityFinding.id)"), 'first-screen highest-priority finding action must open the exact Finding');
assert(dashboard.includes("onOpenMaterials={() => navigateToProjectPath('/materials', project)}"), 'dashboard enterprise-material navigation must use the canonical Materials surface');
assert(dashboard.includes('<details className="card mb-4 dashboard-more-actions">'), 'secondary dashboard actions must be demoted below the first-screen decision');
assert(!dashboard.includes("role: '项目经理'"), 'dashboard must not frame the validation surface as enterprise project-management workflow');

for (const label of ['已确认问题', '已确认 P0', '已确认 P1', '真实证据包', '现在最应该做', '最高优先问题']) {
  assert(hero.includes(label), `dashboard decision hero missing customer-first label: ${label}`);
}
assert(hero.includes('<FindingVerificationStatus finding={focusFinding} compact />'), 'highest-priority finding must reuse the shared verification status');
assert(hero.includes('这里不根据“0 个问题”、测试点数量或前端评分自行推导安全'), 'dashboard first screen must explicitly reject frontend safety inference');
assert(!hero.includes('等效测试点'), 'equivalent test points belong below the decision first screen');
assert(!hero.includes('触达业务模块'), 'module reach belongs below the decision first screen');
assert(!hero.includes('结论可靠度'), 'evidence trust scoring must not compete with the first-screen release decision');

assert(heroStyles.includes('grid-template-columns: minmax(0, .9fr) minmax(0, 1.4fr);'), 'dashboard hero must keep next action and priority finding readable on desktop');
assert(heroStyles.includes('@media (max-width: 760px)'), 'dashboard hero must have a mobile breakpoint');
assert(heroStyles.includes('grid-template-columns: minmax(0, 1fr);'), 'dashboard decision cards must stack on narrow screens');

assert(packageJson.includes('"test:dashboard-decision-first": "node scripts/dashboard-decision-first-contract.mjs"'), 'package script missing dashboard decision-first contract');
assert(ciGate.includes('"test:dashboard-decision-first"'), 'ci gate missing dashboard decision-first contract');

console.log('dashboard decision-first contract passed');
