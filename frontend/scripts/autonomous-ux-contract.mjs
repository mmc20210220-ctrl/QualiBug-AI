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

const [scenarioSelector, fixtureSelector, runCenter, serviceForm, sidebar] = await Promise.all([
  source('src/components/run/RunUploadScenarioSelector.tsx'),
  source('src/components/run/RunUploadFixtureSelector.tsx'),
  source('src/api/run-center.ts'),
  source('src/components/settings/SettingsServiceForm.tsx'),
  source('src/components/Sidebar.tsx'),
]);

requireText(scenarioSelector, '已批准的 UI 场景自动纳入本次验证', 'upload scenario selector');
requireText(scenarioSelector, '用户不需要逐项选择或重复确认', 'upload scenario selector');
forbidText(scenarioSelector, 'type="checkbox"', 'upload scenario selector');

requireText(fixtureSelector, 'setSelectedScenarios(approved.map(scenarioRef))', 'upload fixture selector');
requireText(fixtureSelector, '<details className="run-fixture-selector">', 'upload fixture selector');
requireText(fixtureSelector, '异常补充入口', 'upload fixture selector');
forbidText(fixtureSelector, 'localStorage', 'upload fixture selector');
forbidText(fixtureSelector, 'sessionStorage', 'upload fixture selector');

requireText(runCenter, 'async function activeApprovedScenarioIds', 'run center');
requireText(runCenter, 'await listUploadScenarios(project, false)', 'run center');
requireText(runCenter, "scenario.status === 'active' && scenario.authority === 'approved_copy'", 'run center');
forbidText(runCenter, 'localStorage', 'run center');
forbidText(runCenter, 'sessionStorage', 'run center');

requireText(serviceForm, '最小接入', 'service onboarding form');
requireText(serviceForm, '只需提供系统名称、测试地址和可用凭据', 'service onboarding form');
requireText(serviceForm, '<details className="settings-auth-section">', 'service onboarding form');

requireText(sidebar, "label: '主流程'", 'sidebar');
requireText(sidebar, "label: '高级视图'", 'sidebar');
requireText(sidebar, '少配置 · 自动理解 · 真实验证', 'sidebar');

process.stdout.write('autonomous UX contract: OK\n');
