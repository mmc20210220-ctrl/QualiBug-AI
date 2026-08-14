import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8').replace(/\r\n/g, '\n');
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const guide = read('src/components/settings/SettingsOnboardingGuide.tsx');
const materialsHandoff = read('src/components/materials/MaterialsOnboardingHandoff.tsx');
const materials = read('src/pages/Materials.tsx');
const materialTypes = read('src/lib/material-type-presentation.ts');
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

assert(materialTypes.includes('Friendly labels only. This map is intentionally NOT an allowlist.'), 'material type aliases must be documented as display-only');
assert(materialTypes.includes("ui_ux: 'UI / UX 设计'"), 'shared material type presentation must provide a friendly UI/UX label');
assert(materialTypes.includes("return MATERIAL_TYPE_LABELS[type] || type;"), 'unknown source_type values must fall back to their real backend value');
assert(materialTypes.includes("return String(value || '').trim().toLowerCase() || 'unclassified';"), 'material source types must normalize without filtering unknown values');

assert(materialsHandoff.includes("location.pathname !== '/materials'"), 'materials onboarding handoff must stay scoped to the materials page');
assert(materialsHandoff.includes('getKnowledgeAsset(project)'), 'materials handoff must read real knowledge asset state');
assert(materialsHandoff.includes('listKnowledgeConnectors(project)'), 'materials readiness must read real online connector inventory');
assert(materialsHandoff.includes('Promise.allSettled(['), 'materials readiness must preserve partial truth when one readiness source cannot be read');
assert(materialsHandoff.includes('materialReadError'), 'material readiness failure must remain distinct from connector inventory failure');
assert(materialsHandoff.includes('connectorReadError'), 'connector inventory failure must remain distinct from material readiness failure');
assert(materialsHandoff.includes('onlineActive'), 'materials handoff must distinguish online active sources');
assert(materialsHandoff.includes('uploadedActive'), 'materials handoff must distinguish uploaded supplement sources');
assert(materialsHandoff.includes('observedTypeCount'), 'materials readiness must expose observed source-type diversity without a fixed denominator');
assert(materialsHandoff.includes('activeTypeCounts'), 'materials readiness must retain dynamic active source-type counts');
assert(materialsHandoff.includes('normalizeMaterialSourceType(source.source_type)'), 'materials readiness must use the shared open-ended source-type normalizer');
assert(materialsHandoff.includes('activeSources.forEach((source) => {'), 'materials readiness must derive type distribution from every real active source');
assert(materialsHandoff.includes('activeTypeCounts[type] = (activeTypeCounts[type] || 0) + 1;'), 'dynamic source types must be counted without an allowlist');
assert(materialsHandoff.includes('materialSourceTypeLabel(type)'), 'materials readiness must use the shared display-only source-type label helper');
assert(!materialsHandoff.includes('BUSINESS_CONTEXT_TYPES'), 'materials readiness must not use a fixed business-context source-type allowlist');
assert(!materialsHandoff.includes('BusinessInputCounts'), 'materials readiness must not hard-code a finite business-input category model');
assert(!materialsHandoff.includes('businessInputCategoryCount'), 'materials readiness must not use a fixed category count denominator');
assert(!materialsHandoff.includes('businessContextActive'), 'material readiness/blocking must not depend on a fixed business-context type subset');

assert(materialsHandoff.includes('1. 在线来源已连接'), 'materials page must expose online-source connection as readiness stage one');
assert(materialsHandoff.includes('2. 资料已同步'), 'materials page must expose material sync as readiness stage two');
assert(materialsHandoff.includes('3. 业务理解输入'), 'materials page must expose business-understanding input as readiness stage three');
assert(materialsHandoff.includes('连接成功不等于资料已经完成同步'), 'connector-ready and material-ready must remain distinct');
assert(materialsHandoff.includes('必须先形成真实 active source'), 'business-understanding input readiness must require a real active material source');
assert(materialsHandoff.includes('${snapshot.active} 份资料已进入输入主链'), 'all real active sources must be allowed to enter the presentation input mainline');
assert(materialsHandoff.includes('不设固定类型白名单'), 'business-understanding input presentation must explicitly reject a fixed source-type whitelist');
assert(materialsHandoff.includes('资料类型分布'), 'materials readiness must expose dynamic source-type distribution');
assert(materialsHandoff.includes('已观察到 ${snapshot.observedTypeCount} 类 active 资料'), 'type distribution must use a dynamic observed count without a fixed denominator');
assert(materialsHandoff.includes('未知类型会原样展示'), 'unknown backend source types must remain visible to the customer');
assert(!materialsHandoff.includes('/5 类核心输入'), 'materials readiness must not present a fixed five-type denominator');
assert(!materialsHandoff.includes('核心输入覆盖'), 'materials readiness must not frame a fixed source subset as canonical enterprise input coverage');
assert(!materialsHandoff.includes('理解完成率'), 'materials type distribution must not be labeled as understanding completion rate');

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
const missingSourcePriority = materialsHandoff.indexOf('if (snapshot.connectorCount === 0 && snapshot.active === 0)');
const connectedNoMaterialPriority = materialsHandoff.indexOf('if (snapshot.connectorCount > 0 && snapshot.active === 0)');
const uploadedOnlyPriority = materialsHandoff.indexOf('if (snapshot.onlineActive === 0 && snapshot.uploadedActive > 0)');
assert(
  readFailurePriority >= 0
  && authorizationPriority > readFailurePriority
  && inactivePriority > authorizationPriority
  && syncFailurePriority > inactivePriority
  && downstreamPriority > syncFailurePriority
  && syncingPriority > downstreamPriority
  && partialCoveragePriority > syncingPriority
  && missingSourcePriority > partialCoveragePriority
  && connectedNoMaterialPriority > missingSourcePriority
  && uploadedOnlyPriority > connectedNoMaterialPriority,
  'materials blocker priority must keep state-read/auth/sync/coverage issues ahead of lower-priority source guidance',
);
assert(!materialsHandoff.includes('if (snapshot.observedTypeCount'), 'source-type diversity must never become a readiness blocker');
assert(!materialsHandoff.includes('if (snapshot.activeTypeCounts'), 'source-type membership must never become a readiness blocker');

assert(materialsHandoff.includes('当前至少一个真实状态接口不可用'), 'read failures must fail closed instead of synthesizing readiness');
assert(materialsHandoff.includes('这是当前最高优先级阻塞'), 'authorization failure must be surfaced as the highest actionable connector blocker');
assert(materialsHandoff.includes('失败项不会被包装成可用输入'), 'failed sync or material processing must not become usable input');
assert(materialsHandoff.includes('前端只说明下游刷新未完成'), 'downstream degradation must not imply completed business understanding');
assert(materialsHandoff.includes('部分资源未覆盖'), 'partial connector coverage must remain visible');
assert(materialsHandoff.includes('文件补充已经可用，不会阻塞首次运行'), 'uploaded-only materials must remain a non-blocking fallback');
assert(materialsHandoff.includes('任何后端真实识别并成功接入的资料都可以成为输入'), 'ready input presentation must remain open-ended across source types');
assert(materialsHandoff.includes('当前最重要动作'), 'materials page must expose one clear highest-priority next action');
assert(materialsHandoff.includes('只显示当前最高优先级动作'), 'materials page must explain that the CTA is prioritized rather than exhaustive');
assert(materialsHandoff.includes('<button type="button" className="btn btn-primary" onClick={handleNextAction}'), 'materials readiness surface must expose exactly one primary prioritized action');
assert(materialsHandoff.includes("document.querySelector('.materials-primary-card')"), 'online-source actions must navigate to the real connector section');
assert(materialsHandoff.includes("document.querySelector('.materials-inventory-card')"), 'material failure actions must navigate to the real material inventory');
assert(materialsHandoff.includes("navigateToProjectPath('/settings', project)"), 'ready material flow must preserve project context when moving to system setup');
assert(layout.includes('<MaterialsOnboardingHandoff />'), 'layout must mount the materials readiness surface above the materials page');

assert(materials.includes("from '../lib/material-type-presentation'"), 'materials page must use the shared open-ended material type presentation helper');
assert(!materials.includes('EXECUTABLE_SOURCE_TYPES'), 'materials page must not define executable materials through a fixed source-type whitelist');
assert(!materials.includes('可执行资料'), 'materials summary must not label a fixed source subset as executable materials');
assert(!materials.includes('核心资料覆盖'), 'materials page must not present PRD/API/DB as the canonical enterprise material set');
assert(!materials.includes("const prdCount ="), 'materials page must not derive readiness from a fixed PRD counter');
assert(!materials.includes("const apiCount ="), 'materials page must not derive readiness from a fixed API counter');
assert(!materials.includes("const dbCount ="), 'materials page must not derive readiness from a fixed DB counter');
assert(materials.includes("const activeSources = sources.filter((item) => item.status === 'active');"), 'materials page type structure must be based on real active sources');
assert(materials.includes('const observedTypeCount = sourceTypeCounts.size;'), 'materials page must expose dynamic observed type count without a fixed denominator');
assert(materials.includes('normalizeMaterialSourceType(item.source_type)'), 'materials page must count source types through the shared open-ended normalizer');
assert(materials.includes('materialSourceTypeLabel(type)'), 'materials page must render dynamic source types through the shared display helper');
assert(materials.includes("{ label: '资料类型', value: observedTypeCount"), 'materials page summary must show dynamic source-type count');
assert(materials.includes('资料来源结构'), 'materials page must explain online versus uploaded source structure instead of fixed core categories');
assert(materials.includes('资料类型结构'), 'materials page must expose dynamic material type structure');
assert(materials.includes('未知类型会原样展示'), 'materials page must explicitly preserve unknown backend source types');
assert(materials.includes('当前上传分类只代表后端已提供的显式入口，不代表企业资料类型全集'), 'manual upload classifications must not be presented as the enterprise material type universe');
assert(materials.includes('<option value="ui_ux">UI / UX 设计 / 原型</option>'), 'manual supplement upload must keep the existing UI/UX classification');

const onlineSection = materials.indexOf('<h2>在线连接器</h2>');
const uploadSection = materials.indexOf('<h2>离线资料上传</h2>');
assert(onlineSection >= 0, 'materials page must expose online connectors as the primary materials surface');
assert(uploadSection > onlineSection, 'file upload must remain after the online connector surface');
assert(materials.includes('<span className="settings-hero-kicker">补充方式</span>'), 'file upload must be explicitly framed as a supplement');
assert(materials.includes('接入在线资料'), 'materials page primary CTA must remain online-source connection');

assert(journey.includes("title: '接入被测系统'"), 'first-run journey must start from real system setup');
assert(journey.includes("title: '连接企业资料'"), 'first-run journey must use online-first enterprise materials wording');
assert(journey.includes('优先连接企业在线文档或知识库持续同步，缺失资料再用文件上传补充'), 'journey must explain online-first and upload-second materials strategy');
assert(journey.includes("path: '/materials', action: '连接资料源'"), 'journey materials action must enter the canonical connection surface');
assert(journey.includes("title: '运行前检查并检测'"), 'first-run journey must describe the preflight boundary before scanning');
assert(journey.includes("path: '/campaigns', action: '检查并运行'"), 'first-run run step must enter the real run center');
assert(journey.includes("title: '查看结果与发布建议'"), 'first-run journey must be result-first after scanning');
assert(journey.includes("path: '/dashboard', action: '查看价值总览'"), 'first-run result step must lead to dashboard instead of assuming findings exist');

const mainNavStart = sidebar.indexOf("label: '主导航'");
assert(mainNavStart >= 0, 'sidebar navigation must converge to a single customer navigation section');
const mainNavBlock = sidebar.slice(mainNavStart, sidebar.indexOf('];', mainNavStart));
for (const item of ["to: 'dashboard'", "to: 'findings'", "to: 'integration'"]) {
  assert(mainNavBlock.includes(item), `sidebar must keep the converged customer destination: ${item}`);
}
// 技术/内部页面退出客户一级导航（保留 URL 直访），不得再占用侧边栏。
for (const internal of ["to: 'release'", "to: 'coverage'", "to: 'jobs'", "to: 'campaigns'", "to: 'evidence'", "to: 'materials'", "to: 'settings'"]) {
  assert(!mainNavBlock.includes(internal), `internal destination must exit customer navigation: ${internal}`);
}
assert(sidebar.includes("import { useLiveStatus, useProjectSummary } from '../api/data';"), 'sidebar status must consume real scan materialization state');
assert(sidebar.includes('const { scanActive, hasMaterializedMetrics } = useLiveStatus(project, 15_000);'), 'sidebar must distinguish an active or completed scan from a never-run project');
assert(sidebar.includes("? '检测进行中'"), 'sidebar must expose an active scan before defect summary states');
assert(sidebar.includes("? '本轮暂无已确认问题'\n              : '等待首次验证';"), 'clean materialized scans must not be mislabeled as never verified');

assert(runCenter.includes('const preflightReady = Boolean(preflight?.ready);'), 'run center must keep backend preflight as execution authority');
assert(runCenter.includes('if (!preflightReady) {'), 'run center handler must remain fail-closed after frontend onboarding completion');
assert(runCenter.includes('runDisabled={runDisabled}'), 'run preflight snapshot must receive the same real submission lock');

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
