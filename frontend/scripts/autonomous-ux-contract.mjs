import { readFile } from 'node:fs/promises';
import process from 'node:process';

const root = new URL('../', import.meta.url);

async function source(path) {
  const content = await readFile(new URL(path, root), 'utf8');
  return content.replace(/\r\n/g, '\n');
}

function requireText(content, expected, label) {
  if (!content.includes(expected)) {
    throw new Error(`${label}: missing required autonomous UX contract text: ${expected}`);
  }
}

function forbidText(content, forbidden, label) {
  if (content.includes(forbidden)) {
    throw new Error(`${label}: forbidden user-maintained interaction returned: ${forbidden}`);
  }
}

function requireAll(content, expected, label) {
  for (const value of expected) requireText(content, value, label);
}

function forbidAll(content, forbidden, label) {
  for (const value of forbidden) forbidText(content, value, label);
}

const [
  scenarioSelector,
  fixtureSelector,
  runCenter,
  campaignsPage,
  dashboardPage,
  dashboardUnderstanding,
  serviceForm,
  customerSection,
  understandingReceipt,
  knowledgeApi,
  topologySection,
  metadataSection,
  llmSection,
  advancedToolsSection,
  runPreflightPresentation,
  ingestHandler,
  projectAssets,
  understandingPreflight,
  commandCenterUnderstanding,
  serviceComposition,
  sidebar,
] = await Promise.all([
  source('src/components/run/RunUploadScenarioSelector.tsx'),
  source('src/components/run/RunUploadFixtureSelector.tsx'),
  source('src/api/run-center.ts'),
  source('src/pages/EnterpriseCampaigns.tsx'),
  source('src/pages/Dashboard.tsx'),
  source('src/components/dashboard/EnterpriseUnderstandingPanel.tsx'),
  source('src/components/settings/SettingsServiceForm.tsx'),
  source('src/components/settings/SettingsCustomerSection.tsx'),
  source('src/components/settings/EnterpriseUnderstandingReceipt.tsx'),
  source('src/api/knowledge-ingest.ts'),
  source('src/components/settings/SettingsTopologySection.tsx'),
  source('src/components/settings/SettingsMetadataSection.tsx'),
  source('src/components/settings/SettingsLlmSection.tsx'),
  source('src/components/settings/SettingsAdvancedToolsSection.tsx'),
  source('src/lib/run-preflight-presentation.ts'),
  source('../ai_test_asset_center/private_pilot_ingest_handlers.py'),
  source('../ai_test_asset_center/private_pilot_project_assets.py'),
  source('../ai_test_asset_center/private_pilot_understanding_preflight.py'),
  source('../ai_test_asset_center/private_pilot_command_center_understanding.py'),
  source('../ai_test_asset_center/private_pilot_service.py'),
  source('src/components/Sidebar.tsx'),
]);

requireAll(scenarioSelector, [
  '已批准的 UI 场景自动纳入本次验证',
  '用户不需要逐项选择或重复确认',
], 'upload scenario selector');
forbidText(scenarioSelector, 'type="checkbox"', 'upload scenario selector');

requireAll(fixtureSelector, [
  'const refs = approved.map(scenarioRef)',
  'reportScenarioState({ refs, loading: false, error:',
  'onScenarioStateChange?:',
  'onScenarioSelectionChange?:',
  '<details className="run-fixture-selector">',
  '异常补充入口',
], 'upload fixture selector');
forbidAll(fixtureSelector, ['localStorage', 'sessionStorage'], 'upload fixture selector');

requireAll(runCenter, [
  'async function activeApprovedScenarioIds',
  'await listUploadScenarios(project, false)',
  "scenario.status === 'active' && scenario.authority === 'approved_copy'",
  "const forceReadOnly = options.execution_mode === 'safe_read_only';",
  'const fixtureIds = forceReadOnly',
  'const scenarioIds = forceReadOnly',
], 'run center');
forbidAll(runCenter, ['localStorage', 'sessionStorage'], 'run center');

requireAll(campaignsPage, [
  '后台会自动选择目标服务、有效资料快照、登录方式、测试数据方案和可执行场景',
  '<details className="card mb-4">',
  '异常覆盖与安全熔断',
  '强制只读熔断：本次验证禁止任何写入',
  'onScenarioStateChange={handleScenarioStateChange}',
  '个已审批 UI 场景由后台自动纳入',
], 'campaigns page');
forbidAll(campaignsPage, ['测试数据策略', 'type DataStrategy', 'buildTestDataContract'], 'campaigns page');

requireAll(runPreflightPresentation, [
  "primaryActionLabel: '开始企业系统验证'",
], 'run preflight presentation');

requireAll(dashboardPage, [
  'import { EnterpriseUnderstandingPanel }',
  'const knowledgeSummary = asRecord(record.knowledge_summary);',
  '<EnterpriseUnderstandingPanel',
  "path: '/settings'",
  'navigateToProjectPath(nextAction.path, project)',
], 'dashboard understanding projection');

requireAll(dashboardUnderstanding, [
  '已有知识资产的只读投影',
  'summary.understanding_gates',
  'summary.understanding_blocker_receipts',
  'summary.understanding_source_receipt_count',
  "key: 'runtime_plan'",
  "label: 'Runtime Plan'",
  "key: 'runtime_materialization'",
  "label: '运行实例化'",
  '运行准备链已闭合',
  '当前草案本身仍不可直接发送或执行。',
  '实例化草案',
  '运行实例化缺口',
  '查看缺口与原始资料回执',
  '现有门禁回执尚未附具体资料定位',
  '没有证据的条目不会被系统猜测补齐',
  '旧 Probe 回退绕过门禁',
], 'dashboard enterprise understanding');
forbidAll(dashboardUnderstanding, ['contentEditable', '保存模型', '确认理解正确'], 'dashboard enterprise understanding');

requireAll(serviceForm, [
  '最小接入',
  '只需提供系统名称、测试地址和可用凭据',
  '<details className="settings-auth-section">',
], 'service onboarding form');

requireAll(customerSection, [
  '这里仅负责选择或创建客户工作区',
  '在线资料源作为主入口持续同步，文件上传只用于补充在线来源没有覆盖的资料',
  '优先连接企业在线资料',
  'Settings 不维护第二套资料流程',
  "navigateToProjectPath('/materials', project)",
  '连接企业资料',
], 'customer and materials section');
forbidAll(customerSection, ['type="file"', 'multiple', 'handleFilesSelected', '<EnterpriseUnderstandingReceipt'], 'customer section must not duplicate the canonical materials surface');

requireAll(understandingReceipt, [
  '已有知识资产的只读投影',
  'asset.enterprise_understanding_model',
  'asset.enterprise_comprehension_gate',
  'asset.scenario_planning_gate',
  'asset.scenario_ir_gate',
  'asset.scenario_execution_contract_gate',
  'asset.runtime_plan_gate',
  'asset.runtime_plan_unknowns',
  'asset.runtime_materialization_gate',
  'asset.runtime_materialization_unknowns',
  "gateView('Runtime Plan'",
  "gateView('运行实例化'",
  '不可发送请求草稿',
  '不可执行断言草稿',
  '系统不会通过人工点击“确认正确”关闭缺口',
  '不创建第二套模型',
], 'enterprise understanding receipt');
forbidAll(understandingReceipt, ['contentEditable', '保存模型', '确认理解正确'], 'enterprise understanding receipt');

requireAll(understandingPreflight, [
  'load_enterprise_business_knowledge_asset',
  'existing_enterprise_business_knowledge_asset',
  'runtime_plan_gate',
  'runtime_materialization_gate',
  'RUNTIME_PLAN_BLOCKED',
  'RUNTIME_MATERIALIZATION_BLOCKED',
  'runtime_materialization_unknowns',
  'first_blocked_gate',
  '旧 Probe 回退绕过门禁',
  'super()._handle_scan_preflight(project, root, body)',
], 'understanding preflight projection');
forbidAll(understandingPreflight, ['build_enterprise_business_knowledge_asset', '第二套'], 'understanding preflight projection');

requireAll(commandCenterUnderstanding, [
  'load_enterprise_business_knowledge_asset',
  'data["knowledge_summary"] = {**existing, **_understanding_projection(asset)}',
  'runtime_plan_gate',
  'runtime_plan_unknowns',
  'runtime_materialization_gate',
  'runtime_materialization_unknowns',
  'runtime_materialization_ready',
  'formal_runtime_chain_ready',
  'def _source_receipts(',
  '"understanding_blocker_receipts": blocker_receipts',
  '"understanding_source_receipt_count"',
  'EXISTING_KNOWLEDGE_ASSET_GATE_PROJECTION_NOT_SECOND_AUTHORITY',
  'super()._build_command_center(project_id, root)',
], 'command center understanding projection');
forbidText(commandCenterUnderstanding, 'build_enterprise_business_knowledge_asset', 'command center understanding projection');

requireAll(serviceComposition, [
  'from .private_pilot_understanding_preflight import (',
  'UnderstandingPreflightProjectionMixin,\n    ScanHandlersMixin,',
  'from .private_pilot_command_center_understanding import (',
  'UnderstandingCommandCenterProjectionMixin,\n    CommandCenterBuilderMixin,',
], 'private pilot composition');

requireAll(knowledgeApi, [
  "fetch('/api/knowledge/ingest'",
  'filename: file.name',
  'defer_auto_scan: options.deferAutoScan',
  'finalize_batch: options.finalizeBatch',
  'for (let index = 0; index < selected.length; index += 1)',
], 'knowledge ingest API');
forbidAll(knowledgeApi, ['\n      type:', 'localStorage', 'sessionStorage'], 'knowledge ingest API');

requireAll(ingestHandler, [
  'explicit_type = str(\n            body.get("type") or body.get("doc_type") or ""\n        ).strip().lower()',
  'ingest_uploaded_enterprise_material(',
  'type_resolution = str(authority_result.get("type_resolution") or "")',
  'defer_auto_scan = body.get("defer_auto_scan") is True',
  'finalize_batch = body.get("finalize_batch") is True',
  'source_type_resolution',
], 'knowledge ingest backend');
forbidAll(ingestHandler, [
  'body.get("doc_type") or "prd"',
  'raw.decode("utf-8", errors="replace")\n            source_manifest',
], 'knowledge ingest backend');

requireAll(projectAssets, [
  'def resolve_knowledge_source_type(',
  'from .enterprise_knowledge_center import classify_enterprise_knowledge_source',
  'return detected, "explicit" if requested else "automatic"',
], 'knowledge source classifier');
forbidText(projectAssets, 'return "prd"', 'knowledge source classifier');

requireAll(topologySection, [
  '接入被测系统',
  '只提供系统名称、测试环境地址和可用凭据',
  '<details className="settings-auth-section">',
  '不要求用户持续维护',
], 'topology section');

requireAll(metadataSection, [
  '<details className="section-card settings-span-2">',
  '异常覆盖：业务范围与绝对禁触边界',
  '留空则由后台自动判断',
  '不是要求客户维护完整接口清单',
], 'metadata section');

requireAll(llmSection, [
  '<details className="section-card">',
  '属于部署级能力，不应成为每个客户项目的日常维护项',
], 'LLM section');

requireAll(advancedToolsSection, [
  '<details className="section-card settings-span-2">',
  '正常客户流程不需要维护',
  '应优先由后台从企业资料、页面结构和执行轨迹自动生成',
], 'advanced tools and governance section');

requireAll(sidebar, [
  "label: '主流程'",
  "label: '高级视图'",
  '少配置 · 自动理解 · 真实验证',
], 'sidebar');

process.stdout.write('autonomous UX contract: OK\n');
