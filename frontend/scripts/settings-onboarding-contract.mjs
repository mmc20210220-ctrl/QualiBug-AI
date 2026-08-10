import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const guide = read('src/components/settings/SettingsOnboardingGuide.tsx');
const materialsHandoff = read('src/components/materials/MaterialsOnboardingHandoff.tsx');
const journey = read('src/components/dashboard/JourneyStrip.tsx');
const customerSection = read('src/components/settings/SettingsCustomerSection.tsx');
const layout = read('src/components/Layout.tsx');
const metadata = read('src/components/settings/SettingsMetadataSection.tsx');
const topology = read('src/components/settings/SettingsTopologySection.tsx');
const serviceForm = read('src/components/settings/SettingsServiceForm.tsx');
const runCenter = read('src/pages/EnterpriseCampaigns.tsx');
const packageJson = read('package.json');
const ciGate = read('scripts/ci-gate.mjs');

for (const label of ['1. 系统地址', '2. 测试账号', '3. 企业资料', '4. 数据库（可选）']) {
  assert(guide.includes(label), `settings onboarding step missing: ${label}`);
}
assert(guide.includes('getKnowledgeAsset(project)'), 'settings onboarding must read real material status');
assert(guide.includes('getServiceCredentials(project)'), 'settings onboarding must read real credential status');
assert(guide.includes('listConnectors(project)'), 'settings onboarding must read real connector status');
assert(guide.includes('const setupReady = requiredCompleted === 3 && !loadWarning;'), 'settings onboarding must require all trusted required states before declaring setup ready');
assert(guide.includes("? { label: '先接入系统地址', kind: 'system' as const }"), 'settings onboarding must route missing system access to the real system section');
assert(guide.includes("? { label: '补充测试账号', kind: 'system' as const }"), 'settings onboarding must route missing auth to the real system section');
assert(guide.includes("? { label: '导入企业资料', kind: 'materials' as const }"), 'settings onboarding must route missing materials to the materials page');
assert(guide.includes("{ label: '继续运行前检查', kind: 'campaigns' as const }"), 'completed setup must expose an actionable run-preflight handoff');
assert(guide.includes("navigateToProjectPath('/campaigns', project);"), 'setup completion must preserve project context when entering run preflight');
assert(guide.includes('重新核对接入状态'), 'partial readiness read failures must be rechecked instead of treated as missing setup');
assert(guide.includes('role="alert"'), 'readiness read warning must be announced as an alert');
assert(metadata.includes('<SettingsOnboardingGuide project={project} />'), 'settings page must render onboarding guide');
assert(topology.includes('id="settings-system-access"'), 'system access section must expose onboarding anchor');

assert(customerSection.includes('企业资料统一在“企业资料”页面接入和维护'), 'settings must explain that enterprise materials have one canonical entry');
assert(customerSection.includes("navigateToProjectPath('/materials', project)"), 'settings customer section must route to the canonical materials page');
assert(customerSection.includes('打开企业资料'), 'settings must expose the canonical materials navigation action');
assert(!customerSection.includes('ingestKnowledgeFiles'), 'settings must not keep a duplicate enterprise-material upload implementation');
assert(!customerSection.includes('type="file"'), 'settings must not render a second enterprise-material file input');

assert(materialsHandoff.includes("location.pathname !== '/materials'"), 'materials onboarding handoff must stay scoped to the materials page');
assert(materialsHandoff.includes('getKnowledgeAsset(project)'), 'materials handoff must read real knowledge asset state');
assert(materialsHandoff.includes('const cleanReady = snapshot.active > 0 && snapshot.processing === 0 && snapshot.failed === 0 && !readError;'), 'materials handoff must not call processing or failed materials clean-ready');
assert(materialsHandoff.includes('不把读取失败解释为资料缺失'), 'materials read failure must not collapse into a missing-material state');
assert(materialsHandoff.includes("navigateToProjectPath('/settings', project)"), 'materials handoff must preserve project context when moving to system setup');
assert(materialsHandoff.includes("navigateToProjectPath('/campaigns', project)"), 'previously configured customers must be able to enter real run preflight from materials');
assert(materialsHandoff.includes('重新核对资料状态'), 'materials handoff must allow immediate recheck while parsing or degraded');
assert(layout.includes('<MaterialsOnboardingHandoff />'), 'layout must mount the materials onboarding handoff above the materials page');

assert(journey.includes("title: '接入被测系统'"), 'first-run journey must start from real system setup');
assert(journey.includes("title: '导入企业资料'"), 'first-run journey must expose enterprise materials');
assert(journey.includes("title: '运行前检查并检测'"), 'first-run journey must describe the preflight boundary before scanning');
assert(journey.includes("path: '/campaigns', action: '检查并运行'"), 'first-run run step must enter the real run center');
assert(journey.includes("title: '查看结果与发布建议'"), 'first-run journey must be result-first after scanning');
assert(journey.includes("path: '/dashboard', action: '查看价值总览'"), 'first-run result step must lead to dashboard instead of assuming findings exist');

assert(runCenter.includes('const preflightReady = Boolean(preflight?.ready);'), 'run center must keep backend preflight as execution authority');
assert(runCenter.includes('if (!preflightReady) {'), 'run center handler must remain fail-closed after frontend onboarding completion');
assert(runCenter.includes('disabled={runDisabled}'), 'run center button must remain disabled when real preflight blocks execution');

assert(serviceForm.includes('window.sessionStorage.setItem'), 'service setup must autosave a session draft');
assert(serviceForm.includes('window.sessionStorage.removeItem'), 'successful service setup must clear its session draft');

const startMarker = '// onboarding-draft:start';
const endMarker = '// onboarding-draft:end';
const start = serviceForm.indexOf(startMarker);
const end = serviceForm.indexOf(endMarker);
assert(start >= 0 && end > start, 'onboarding draft security markers missing');
const draftBlock = serviceForm.slice(start, end);
for (const forbidden of ['roleAccounts', 'bearerToken', 'apiKey', 'dbUser', 'dbPass']) {
  assert(!draftBlock.includes(forbidden), `secret field leaked into onboarding draft: ${forbidden}`);
}

assert(packageJson.includes('"test:settings-onboarding": "node scripts/settings-onboarding-contract.mjs"'), 'package script missing settings onboarding contract');
assert(ciGate.includes('"test:settings-onboarding"'), 'ci gate missing settings onboarding contract');

console.log('settings onboarding contract passed');
