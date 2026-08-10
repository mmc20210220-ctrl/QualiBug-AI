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
const materials = read('src/pages/Materials.tsx');
const journey = read('src/components/dashboard/JourneyStrip.tsx');
const customerSection = read('src/components/settings/SettingsCustomerSection.tsx');
const sidebar = read('src/components/Sidebar.tsx');
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
assert(guide.includes('listConnectors(project)'), 'settings onboarding must read real system connector status');
assert(guide.includes('listKnowledgeConnectors(project)'), 'settings onboarding must distinguish connected online knowledge sources before first materialization');
assert(guide.includes('knowledgeConnectorCount'), 'settings onboarding must expose connected online knowledge source state');
assert(guide.includes('onlineMaterialCount'), 'settings onboarding must distinguish online materials from uploaded supplements');
assert(guide.includes('uploadedMaterialCount'), 'settings onboarding must preserve uploaded supplements as a secondary source');
assert(guide.includes("String(source.source_origin || '').toUpperCase() === 'ONLINE_CONNECTOR'"), 'online material detection must use backend source origin');
assert(guide.includes("String(source.source_ref || '').startsWith('connector://')"), 'online material detection must preserve connector-ref compatibility');
assert(guide.includes('const setupReady = requiredCompleted === 3 && !loadWarning;'), 'settings onboarding must require all trusted required states before declaring setup ready');
assert(guide.includes("? { label: '先接入系统地址', kind: 'system' as const }"), 'settings onboarding must route missing system access to the real system section');
assert(guide.includes("? { label: '补充测试账号', kind: 'system' as const }"), 'settings onboarding must route missing auth to the real system section');
assert(guide.includes("snapshot.knowledgeConnectorCount > 0 ? '查看在线资料同步' : '连接企业在线资料'"), 'missing ready materials must distinguish an already-connected online source from a truly missing connection');
assert(guide.includes('在线资料源已经连接，但首次同步尚未形成真实可读资料'), 'connected-but-not-materialized online sources must not be called ready');
assert(guide.includes('已有文件补充，可继续运行前检查；建议连接企业在线资料'), 'uploaded-only readiness must remain usable while recommending online sources');
assert(guide.includes("{ label: '继续运行前检查', kind: 'campaigns' as const }"), 'completed setup must expose an actionable run-preflight handoff');
assert(guide.includes("navigateToProjectPath('/campaigns', project);"), 'setup completion must preserve project context when entering run preflight');
assert(guide.includes('重新核对接入状态'), 'partial readiness read failures must be rechecked instead of treated as missing setup');
assert(guide.includes('role="alert"'), 'readiness read warning must be announced as an alert');
assert(metadata.includes('<SettingsOnboardingGuide project={project} />'), 'settings page must render onboarding guide');
assert(topology.includes('id="settings-system-access"'), 'system access section must expose onboarding anchor');

assert(customerSection.includes('在线资料源作为主入口持续同步，文件上传只用于补充'), 'settings must explain online-first enterprise materials');
assert(customerSection.includes("navigateToProjectPath('/materials', project)"), 'settings customer section must route to the canonical materials page');
assert(customerSection.includes('连接企业资料'), 'settings must expose the canonical online-first materials navigation action');
assert(!customerSection.includes('ingestKnowledgeFiles'), 'settings must not keep a duplicate enterprise-material upload implementation');
assert(!customerSection.includes('type="file"'), 'settings must not render a second enterprise-material file input');

assert(materialsHandoff.includes("location.pathname !== '/materials'"), 'materials onboarding handoff must stay scoped to the materials page');
assert(materialsHandoff.includes('getKnowledgeAsset(project)'), 'materials handoff must read real knowledge asset state');
assert(materialsHandoff.includes('onlineActive'), 'materials handoff must distinguish online active sources');
assert(materialsHandoff.includes('uploadedActive'), 'materials handoff must distinguish uploaded supplement sources');
assert(materialsHandoff.includes('const cleanReady = snapshot.active > 0 && snapshot.processing === 0 && snapshot.failed === 0 && !readError;'), 'materials handoff must not call processing or failed materials clean-ready');
assert(materialsHandoff.includes('const onlyUploadedReady = cleanReady && snapshot.onlineActive === 0 && snapshot.uploadedActive > 0;'), 'uploaded-only materials must stay ready but visibly secondary');
assert(materialsHandoff.includes('连接在线资料（推荐）'), 'uploaded-only customers must be guided toward online materials without being blocked');
assert(materialsHandoff.includes('暂用补充资料，继续系统与环境'), 'uploaded supplements must remain a valid non-blocking fallback');
assert(materialsHandoff.includes("document.querySelector('.materials-primary-card')"), 'online-first recommendation must navigate to the real online connector section');
assert(materialsHandoff.includes('不把读取失败解释为资料缺失'), 'materials read failure must not collapse into a missing-material state');
assert(materialsHandoff.includes("navigateToProjectPath('/settings', project)"), 'materials handoff must preserve project context when moving to system setup');
assert(materialsHandoff.includes("navigateToProjectPath('/campaigns', project)"), 'previously configured customers must be able to enter real run preflight from materials');
assert(layout.includes('<MaterialsOnboardingHandoff />'), 'layout must mount the materials onboarding handoff above the materials page');

const onlineSection = materials.indexOf('<h2>在线连接器</h2>');
const uploadSection = materials.indexOf('<h2>离线资料上传</h2>');
assert(onlineSection >= 0, 'materials page must expose online connectors as the primary materials surface');
assert(uploadSection > onlineSection, 'file upload must remain after the online connector surface');
assert(materials.includes('<span className="settings-hero-kicker">补充方式</span>'), 'file upload must be explicitly framed as a supplement');
assert(materials.includes('用于补充在线资料没有的 PRD、接口文档、历史缺陷、数据库说明或设计稿'), 'upload copy must explain that files supplement missing online materials');
assert(materials.includes('接入在线资料'), 'materials page primary CTA must remain online-source connection');

assert(journey.includes("title: '接入被测系统'"), 'first-run journey must start from real system setup');
assert(journey.includes("title: '连接企业资料'"), 'first-run journey must use online-first enterprise materials wording');
assert(journey.includes('优先连接企业在线文档或知识库持续同步，缺失资料再用文件上传补充'), 'journey must explain online-first and upload-second materials strategy');
assert(journey.includes("path: '/materials', action: '连接资料源'"), 'journey materials action must enter the canonical connection surface');
assert(journey.includes("title: '运行前检查并检测'"), 'first-run journey must describe the preflight boundary before scanning');
assert(journey.includes("path: '/campaigns', action: '检查并运行'"), 'first-run run step must enter the real run center');
assert(journey.includes("title: '查看结果与发布建议'"), 'first-run journey must be result-first after scanning');
assert(journey.includes("path: '/dashboard', action: '查看价值总览'"), 'first-run result step must lead to dashboard instead of assuming findings exist');

const mainNavStart = sidebar.indexOf("label: '主流程'");
const projectNavStart = sidebar.indexOf("label: '项目接入'");
const advancedNavStart = sidebar.indexOf("label: '高级视图'");
assert(mainNavStart >= 0 && projectNavStart > mainNavStart && advancedNavStart > projectNavStart, 'sidebar navigation sections must keep a stable main/setup/advanced hierarchy');
const mainNavBlock = sidebar.slice(mainNavStart, projectNavStart);
const advancedNavBlock = sidebar.slice(advancedNavStart, sidebar.indexOf('];', advancedNavStart));
assert(mainNavBlock.includes("to: 'release'"), 'release gate must remain a customer main-flow destination');
assert(!advancedNavBlock.includes("to: 'release'"), 'release gate must not regress back into advanced views');
assert(advancedNavBlock.includes("to: 'coverage'") && advancedNavBlock.includes("to: 'jobs'"), 'coverage and background jobs belong in advanced views');
assert(sidebar.includes("import { useLiveStatus, useProjectSummary } from '../api/data';"), 'sidebar status must consume real scan materialization state');
assert(sidebar.includes('const { scanActive, hasMaterializedMetrics } = useLiveStatus(project, 15_000);'), 'sidebar must distinguish an active or completed scan from a never-run project');
assert(sidebar.includes("? '检测进行中'"), 'sidebar must expose an active scan before defect summary states');
assert(sidebar.includes("? '本轮暂无已确认问题'\n              : '等待首次验证'"), 'clean materialized scans must not be mislabeled as never verified');

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
