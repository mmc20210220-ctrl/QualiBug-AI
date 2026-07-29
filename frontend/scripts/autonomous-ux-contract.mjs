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
  topologySection,
  metadataSection,
  llmSection,
  infoSection,
  sidebar,
] = await Promise.all([
  source('src/components/run/RunUploadScenarioSelector.tsx'),
  source('src/components/run/RunUploadFixtureSelector.tsx'),
  source('src/api/run-center.ts'),
  source('src/pages/EnterpriseCampaigns.tsx'),
  source('src/components/settings/SettingsServiceForm.tsx'),
  source('src/components/settings/SettingsCustomerSection.tsx'),
  source('src/components/settings/SettingsTopologySection.tsx'),
  source('src/components/settings/SettingsMetadataSection.tsx'),
  source('src/components/settings/SettingsLlmSection.tsx'),
  source('src/components/settings/SettingsInfoSection.tsx'),
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

requireText(customerSection, '选择客户项目', 'customer section');
requireText(customerSection, '<details className="settings-auth-section settings-mt-10">', 'customer section');
requireText(customerSection, '项目内部结构、测试范围和运行策略不需要在这里维护', 'customer section');

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
