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
assert(materialsHandoff.includes('listKnowledgeConnectors(project)'), 'materials readiness must read real online connector inventory');
assert(materialsHandoff.includes('Promise.allSettled(['), 'materials readiness must preserve partial truth when one readiness source cannot be read');
assert(materialsHandoff.includes('materialReadError'), 'material readiness failure must remain distinct from connector inventory failure');
assert(materialsHandoff.includes('connectorReadError'), 'connector inventory failure must remain distinct from material readiness failure');
assert(materialsHandoff.includes('onlineActive'), 'materials handoff must distinguish online active sources');
assert(materialsHandoff.includes('uploadedActive'), 'materials handoff must distinguish uploaded supplement sources');
assert(materialsHandoff.includes('businessContextActive'), 'materials handoff must distinguish active business-context inputs from generic readable materials');
assert(materialsHandoff.includes('const BUSINESS_CONTEXT_TYPES = new Set(['), 'business-understanding input readiness must use an explicit frontend presentation allowlist');
for (const sourceType of ['prd', 'openapi', 'database_schema', 'collaboration_document', 'historical_bug']) {
  assert(materialsHandoff.includes(`'${sourceType}'`), `business-understanding input readiness missing source type: ${sourceType}`);
}
assert(materialsHandoff.includes('1. 在线来源已连接'), 'materials page must expose online-source connection as readiness stage one');
assert(materialsHandoff.includes('2. 资料已同步'), 'materials page must expose material sync as readiness stage two');
assert(materialsHandoff.includes('3. 业务理解输入'), 'materials page must expose business-understanding input as readiness stage three');
assert(materialsHandoff.includes('连接成功不等于资料已经完成同步'), 'connector-ready and material-ready must remain distinct');
assert(materialsHandoff.includes('这里只代表输入可用，不代表理解正确率或完整性'), 'business-understanding input readiness must not claim understanding quality');
assert(materialsHandoff.includes('必须先形成真实 active source'), 'business-understanding input readiness must require a real active material source');

assert(materialsHandoff.includes('type BusinessInputCounts = {'), 'materials readiness must preserve typed category counts for business inputs');
assert(materialsHandoff.includes('businessInputCategoryCount'), 'materials readiness must count observed business-input categories separately from source count');
assert(materialsHandoff.includes('businessInputCounts'), 'materials readiness must retain per-category active source counts');
assert(materialsHandoff.includes("prd: activeSources.filter((source) => sourceType(source) === 'prd').length"), 'PRD input coverage must come from real active PRD sources');
assert(materialsHandoff.includes("api: activeSources.filter((source) => sourceType(source) === 'openapi').length"), 'API input coverage must come from real active OpenAPI sources');
assert(materialsHandoff.includes("['database_schema', 'db_design'].includes(sourceType(source))"), 'DB input coverage must combine the real DB source types');
assert(materialsHandoff.includes("collaboration: activeSources.filter((source) => sourceType(source) === 'collaboration_document').length"), 'collaboration input coverage must come from real active collaboration sources');
assert(materialsHandoff.includes("historicalBug: activeSources.filter((source) => sourceType(source) === 'historical_bug').length"), 'historical-bug input coverage must come from real active historical bug sources');
for (const label of ['PRD / 需求', 'API / 接口', 'DB / 数据结构', '协作文档', '历史 Bug']) {
  assert(materialsHandoff.includes(label), `business input coverage missing customer category: ${label}`);
}
assert(materialsHandoff.includes('核心输入覆盖'), 'materials page must expose explainable business-input coverage');
assert(materialsHandoff.includes('已观察到 ${snapshot.businessInputCategoryCount}/5 类核心输入'), 'coverage headline must describe observed categories instead of completion percentage');
assert(materialsHandoff.includes('— 未观察到'), 'unseen input categories must be presented as unobserved rather than mandatory failures');
assert(materialsHandoff.includes('“未观察到”不等于企业必须补充'), 'missing category copy must not convert the five categories into mandatory requirements');
assert(materialsHandoff.includes('它不是完成率、理解准确率或新的运行门禁'), 'business input coverage must not impersonate understanding quality or execution authority');
assert(!materialsHandoff.includes('businessInputCategoryCount < 5'), 'five-category input coverage must never become a frontend readiness gate');
assert(!materialsHandoff.includes('理解完成率'), 'business input coverage must not be labeled as understanding completion rate');

assert(materialsHandoff.includes('function readConnectorSnapshot(connectors'), 'materials readiness must derive connector attention from typed connector records');
assert(materialsHandoff.includes('AUTHORIZATION_HEALTH.has(healthStatus)'), 'authorization attention must come from connector health truth');
assert(materialsHandoff.includes("String(connector.health?.status || '').toUpperCase() === 'DOWNSTREAM_DEGRADED'"), 'downstream semantic refresh degradation must remain explicit');
assert(materialsHandoff.includes("String(connector.coverage?.status || '').toUpperCase() === 'PARTIAL_UNSUPPORTED'"), 'partial unsupported connector coverage must remain explicit');
assert(materialsHandoff.includes('function deriveCurrentBlocker('), 'materials page must derive one prioritized current blocker');

const readFailurePriority = materialsHandoff.indexOf('if (materialReadError || connectorReadError)');
const authorizationPriority = materialsHandoff.indexOf('if (snapshot.authorizationAttentionCount > 0)');
const inactivePriority = materialsHandoff.indexOf('if (snapshot.inactiveConnectorCount > 0)');
const syncFailurePriority = materialsHandoff.indexOf('if (snapshot.syncFailureConnectorCount > 0 || snapshot.failed > 0)');
const downstreamPriority = materialsHandoff.indexOf('if (snapshot.downstreamDegradedCount > 0)');
const syncingPriority = materialsHandoff.indexOf('if (snapshot.syncingConnectorCount > 0 || snapshot.processing > 0)');
const partialCoveragePriority = materialsHandoff.indexOf('if (snapshot.partialCoverageConnectorCount > 0)');
const missingSourcePriority = materialsHandoff.indexOf('if (snapshot.connectorCount === 0 && snapshot.uploadedActive === 0)');
const uploadedOnlyPriority = materialsHandoff.indexOf('if (snapshot.onlineActive === 0 && snapshot.uploadedActive > 0)');
const coreInputPriority = materialsHandoff.indexOf('if (snapshot.businessContextActive === 0)');
assert(
  readFailurePriority >= 0
  && authorizationPriority > readFailurePriority
  && inactivePriority > authorizationPriority
  && syncFailurePriority > inactivePriority
  && downstreamPriority > syncFailurePriority
  && syncingPriority > downstreamPriority
  && partialCoveragePriority > syncingPriority
  && missingSourcePriority > partialCoveragePriority
  && uploadedOnlyPriority > missingSourcePriority
  && coreInputPriority > uploadedOnlyPriority,
  'materials blocker priority must keep state-read/auth/sync/coverage issues ahead of lower-priority input guidance',
);

assert(materialsHandoff.includes('当前至少一个真实状态接口不可用'), 'read failures must fail closed instead of synthesizing readiness');
assert(materialsHandoff.includes('这是当前最高优先级阻塞'), 'authorization failure must be surfaced as the highest actionable connector blocker');
assert(materialsHandoff.includes('失败项不会被包装成可用输入'), 'failed sync or material processing must not become usable input');
assert(materialsHandoff.includes('前端只说明下游刷新未完成'), 'downstream degradation must not imply completed business understanding');
assert(materialsHandoff.includes('部分资源未覆盖'), 'partial connector coverage must remain visible');
assert(materialsHandoff.includes('文件补充已经可用，不会阻塞首次运行'), 'uploaded-only materials must remain a non-blocking fallback');
assert(materialsHandoff.includes('这里只提示输入缺口，不推断后端理解质量'), 'core-input guidance must not claim backend understanding quality');
assert(materialsHandoff.includes('当前最重要动作'), 'materials page must expose one clear highest-priority next action');
assert(materialsHandoff.includes('只显示当前最高优先级动作'), 'materials page must explain that the CTA is prioritized rather than exhaustive');
assert(materialsHandoff.includes('<button type="button" className="btn btn-primary" onClick={handleNextAction}'), 'materials readiness surface must expose exactly one primary prioritized action');
assert(materialsHandoff.includes("document.querySelector('.materials-primary-card')"), 'online-source actions must navigate to the real connector section');
assert(materialsHandoff.includes("document.querySelector('.materials-inventory-card')"), 'material failure actions must navigate to the real material inventory');
assert(materialsHandoff.includes("navigateToProjectPath('/settings', project)"), 'ready material flow must preserve project context when moving to system setup');
assert(layout.includes('<MaterialsOnboardingHandoff />'), 'layout must mount the materials readiness surface above the materials page');

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
