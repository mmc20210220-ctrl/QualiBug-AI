import { readFile } from 'node:fs/promises';
import process from 'node:process';

const root = new URL('../', import.meta.url);

async function source(path) {
  return readFile(new URL(path, root), 'utf8');
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

const [
  scenarioSelector,
  fixtureSelector,
  runCenter,
  campaignsPage,
  serviceForm,
  customerSection,
  understandingReceipt,
  knowledgeApi,
  topologySection,
  metadataSection,
  llmSection,
  infoSection,
  ingestHandler,
  projectAssets,
  understandingPreflight,
  serviceComposition,
  sidebar,
] = await Promise.all([
  source('src/components/run/RunUploadScenarioSelector.tsx'),
  source('src/components/run/RunUploadFixtureSelector.tsx'),
  source('src/api/run-center.ts'),
  source('src/pages/EnterpriseCampaigns.tsx'),
  source('src/components/settings/SettingsServiceForm.tsx'),
  source('src/components/settings/SettingsCustomerSection.tsx'),
  source('src/components/settings/EnterpriseUnderstandingReceipt.tsx'),
  source('src/api/knowledge-ingest.ts'),
  source('src/components/settings/SettingsTopologySection.tsx'),
  source('src/components/settings/SettingsMetadataSection.tsx'),
  source('src/components/settings/SettingsLlmSection.tsx'),
  source('src/components/settings/SettingsInfoSection.tsx'),
  source('../ai_test_asset_center/private_pilot_ingest_handlers.py'),
  source('../ai_test_asset_center/private_pilot_project_assets.py'),
  source('../ai_test_asset_center/private_pilot_understanding_preflight.py'),
  source('../ai_test_asset_center/private_pilot_service.py'),
  source('src/components/Sidebar.tsx'),
]);

requireText(scenarioSelector, '已批准的 UI 场景自动纳入本次验证', 'upload scenario selector');
requireText(scenarioSelector, '用户不需要逐项选择或重复确认', 'upload scenario selector');
forbidText(scenarioSelector, 'type="checkbox"', 'upload scenario selector');

requireText(fixtureSelector, 'const refs = approved.map(scenarioRef)', 'upload fixture selector');
requireText(fixtureSelector, 'reportScenarioState({ refs, loading: false, error:', 'upload fixture selector');
requireText(fixtureSelector, 'onScenarioStateChange?:', 'upload fixture selector');
requireText(fixtureSelector, 'onScenarioSelectionChange?:', 'upload fixture selector');
requireText(fixtureSelector, '<details className="run-fixture-selector">', 'upload fixture selector');
requireText(fixtureSelector, '异常补充入口', 'upload fixture selector');
forbidText(fixtureSelector, 'localStorage', 'upload fixture selector');
forbidText(fixtureSelector, 'sessionStorage', 'upload fixture selector');

requireText(runCenter, 'async function activeApprovedScenarioIds', 'run center');
requireText(runCenter, 'await listUploadScenarios(project, false)', 'run center');
requireText(runCenter, "scenario.status === 'active' && scenario.authority === 'approved_copy'", 'run center');
requireText(runCenter, "const forceReadOnly = options.execution_mode === 'safe_read_only';", 'run center');
requireText(runCenter, 'const fixtureIds = forceReadOnly', 'run center');
requireText(runCenter, 'const scenarioIds = forceReadOnly', 'run center');
forbidText(runCenter, 'localStorage', 'run center');
forbidText(runCenter, 'sessionStorage', 'run center');

requireText(campaignsPage, '开始企业系统验证', 'campaigns page');
requireText(campaignsPage, '后台会自动选择目标服务、有效资料快照、登录方式、测试数据方案和可执行场景', 'campaigns page');
requireText(campaignsPage, '<details className="card mb-4">', 'campaigns page');
requireText(campaignsPage, '异常覆盖与安全熔断', 'campaigns page');
requireText(campaignsPage, '强制只读熔断：本次验证禁止任何写入', 'campaigns page');
requireText(campaignsPage, 'onScenarioSelectionChange={handleScenarioSelectionChange}', 'campaigns page');
requireText(campaignsPage, '个已审批 UI 场景由后台自动纳入', 'campaigns page');
forbidText(campaignsPage, '测试数据策略', 'campaigns page');
forbidText(campaignsPage, 'type DataStrategy', 'campaigns page');
forbidText(campaignsPage, 'buildTestDataContract', 'campaigns page');

requireText(serviceForm, '最小接入', 'service onboarding form');
requireText(serviceForm, '只需提供系统名称、测试地址和可用凭据', 'service onboarding form');
requireText(serviceForm, '<details className="settings-auth-section">', 'service onboarding form');

requireText(customerSection, '客户与企业资料', 'customer and materials section');
requireText(customerSection, '用户不需要判断资料类型、选择解析策略、维护版本或逐项绑定场景', 'customer and materials section');
requireText(customerSection, '选择文件后立即导入', 'customer and materials section');
requireText(customerSection, 'type="file"', 'customer and materials section');
requireText(customerSection, 'multiple', 'customer and materials section');
requireText(customerSection, 'void handleFilesSelected(files)', 'customer and materials section');
requireText(customerSection, '<EnterpriseUnderstandingReceipt', 'customer and materials section');
requireText(customerSection, '查看后台识别的资料来源', 'customer and materials section');
forbidText(customerSection, 'setSelectedSourceType', 'customer and materials section');
forbidText(customerSection, 'onDeleteKnowledge', 'customer and materials section');
forbidText(customerSection, 'onConfirmUnderstanding', 'customer and materials section');

requireText(understandingReceipt, '已有知识资产的只读投影', 'enterprise understanding receipt');
requireText(understandingReceipt, 'asset.enterprise_understanding_model', 'enterprise understanding receipt');
requireText(understandingReceipt, 'asset.enterprise_comprehension_gate', 'enterprise understanding receipt');
requireText(understandingReceipt, 'asset.scenario_planning_gate', 'enterprise understanding receipt');
requireText(understandingReceipt, 'asset.scenario_ir_gate', 'enterprise understanding receipt');
requireText(understandingReceipt, 'asset.scenario_execution_contract_gate', 'enterprise understanding receipt');
requireText(understandingReceipt, '系统不会通过人工点击“确认正确”关闭缺口', 'enterprise understanding receipt');
requireText(understandingReceipt, '不创建第二套模型', 'enterprise understanding receipt');
forbidText(understandingReceipt, 'contentEditable', 'enterprise understanding receipt');
forbidText(understandingReceipt, '保存模型', 'enterprise understanding receipt');
forbidText(understandingReceipt, '确认理解正确', 'enterprise understanding receipt');

requireText(understandingPreflight, 'load_enterprise_business_knowledge_asset', 'understanding preflight projection');
requireText(understandingPreflight, 'existing_enterprise_business_knowledge_asset', 'understanding preflight projection');
requireText(understandingPreflight, 'first_blocked_gate', 'understanding preflight projection');
requireText(understandingPreflight, '系统不会通过人工确认或常识补全绕过门禁', 'understanding preflight projection');
requireText(understandingPreflight, 'super()._handle_scan_preflight(project, root, body)', 'understanding preflight projection');
forbidText(understandingPreflight, 'build_enterprise_business_knowledge_asset', 'understanding preflight projection');
forbidText(understandingPreflight, '第二套', 'understanding preflight projection');

requireText(serviceComposition, 'from .private_pilot_understanding_preflight import UnderstandingPreflightProjectionMixin', 'private pilot composition');
requireText(serviceComposition, 'UnderstandingPreflightProjectionMixin,\n    ScanHandlersMixin,', 'private pilot composition');

requireText(knowledgeApi, "fetch('/api/knowledge/ingest'", 'knowledge ingest API');
requireText(knowledgeApi, 'filename: file.name', 'knowledge ingest API');
requireText(knowledgeApi, 'defer_auto_scan: options.deferAutoScan', 'knowledge ingest API');
requireText(knowledgeApi, 'finalize_batch: options.finalizeBatch', 'knowledge ingest API');
requireText(knowledgeApi, 'for (let index = 0; index < selected.length; index += 1)', 'knowledge ingest API');
forbidText(knowledgeApi, '\n      type:', 'knowledge ingest API');
forbidText(knowledgeApi, 'localStorage', 'knowledge ingest API');
forbidText(knowledgeApi, 'sessionStorage', 'knowledge ingest API');

requireText(ingestHandler, 'explicit_type = str(body.get("type") or body.get("doc_type") or "")', 'knowledge ingest backend');
requireText(ingestHandler, 'resolve_knowledge_source_type(', 'knowledge ingest backend');
requireText(ingestHandler, 'extracted_text = str(doc_info.get("text") or "")', 'knowledge ingest backend');
requireText(ingestHandler, 'defer_auto_scan = body.get("defer_auto_scan") is True', 'knowledge ingest backend');
requireText(ingestHandler, 'finalize_batch = body.get("finalize_batch") is True', 'knowledge ingest backend');
requireText(ingestHandler, 'source_type_resolution', 'knowledge ingest backend');
forbidText(ingestHandler, 'body.get("doc_type") or "prd"', 'knowledge ingest backend');
forbidText(ingestHandler, 'raw.decode("utf-8", errors="replace")\n            source_manifest', 'knowledge ingest backend');

requireText(projectAssets, 'def resolve_knowledge_source_type(', 'knowledge source classifier');
requireText(projectAssets, 'from .enterprise_knowledge_center import _classify_source', 'knowledge source classifier');
requireText(projectAssets, 'return normalized, "automatic"', 'knowledge source classifier');
requireText(projectAssets, 'return normalized, "explicit_override"', 'knowledge source classifier');
forbidText(projectAssets, 'return "prd"', 'knowledge source classifier');

requireText(topologySection, '接入被测系统', 'topology section');
requireText(topologySection, '只提供系统名称、测试环境地址和可用凭据', 'topology section');
requireText(topologySection, '<details className="settings-auth-section">', 'topology section');
requireText(topologySection, '不要求用户持续维护', 'topology section');

requireText(metadataSection, '<details className="section-card settings-span-2">', 'metadata section');
requireText(metadataSection, '异常覆盖：业务范围与绝对禁触边界', 'metadata section');
requireText(metadataSection, '留空则由后台自动判断', 'metadata section');
requireText(metadataSection, '不是要求客户维护完整接口清单', 'metadata section');

requireText(llmSection, '<details className="section-card">', 'LLM section');
requireText(llmSection, '属于部署级能力，不应成为每个客户项目的日常维护项', 'LLM section');

requireText(infoSection, '<details className="section-card settings-span-2">', 'internal governance section');
requireText(infoSection, '正常客户流程不需要维护', 'internal governance section');
requireText(infoSection, '应优先由后台从企业资料、页面结构和执行轨迹自动生成', 'internal governance section');

requireText(sidebar, "label: '主流程'", 'sidebar');
requireText(sidebar, "label: '高级视图'", 'sidebar');
requireText(sidebar, '少配置 · 自动理解 · 真实验证', 'sidebar');

process.stdout.write('autonomous UX contract: OK\n');
