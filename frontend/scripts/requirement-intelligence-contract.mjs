import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(process.cwd());
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};
const requireText = (content, expected, label) => {
  assert(content.includes(expected), `${label}: missing ${expected}`);
};

const app = read('src/App.tsx');
const api = read('src/api/requirement-intelligence.ts');
const page = read('src/pages/RequirementIntelligence.tsx');
const layout = read('src/components/Layout.tsx');
const sidebar = read('src/components/Sidebar.tsx');
const topbar = read('src/components/Topbar.tsx');

for (const expected of [
  "to=\"/requirements\"",
  'path="/requirements" element={<RequirementIntelligence />}',
  'path="/products" element={<PreserveSearchRedirect to="/requirements" />}',
]) requireText(app, expected, 'Requirement Intelligence routing');
requireText(app, 'path="*" element={<PreserveSearchRedirect to="/dashboard" />}', 'legacy route compatibility');

for (const expected of [
  '${API_V1_BASE}/projects/${encodeURIComponent(project)}/requirement-intelligence',
  "const QUALITY_CLAIM = 'DETERMINISTIC_FINDING_GATE_NOT_COMPLETENESS_OR_RECALL';",
  'if (analysisStatus !== status) throw contractError',
  'if (ready !== (status === \'READY\')) throw contractError',
  'if (findingCount !== findings.length) throw contractError',
  "throw contractError('findings[].finding_type')",
]) requireText(api, expected, 'Requirement Intelligence API truth contract');

for (const expected of [
  'Requirement Readiness',
  '跨资料冲突',
  '定义缺失',
  '业务歧义',
  '查看证据',
  '只展示可追溯、已有 authority 支撑的 Finding',
  '不等于资料完整率或问题召回率为 100%',
]) requireText(page, expected, 'Requirement Intelligence workspace');

for (const expected of [
  "const isRequirementWorkspace = location.pathname === '/requirements';",
  '{!isRequirementWorkspace && <RunCustomerResultSummary />}',
  '{!isRequirementWorkspace && <RunLifecycleBanner />}',
]) requireText(layout, expected, 'Requirement Intelligence runtime isolation');

requireText(sidebar, "{ to: 'requirements', icon: 'requirements', label: '需求审查' }", 'Requirement Intelligence primary navigation');
requireText(topbar, "const isRequirementsPage = location.pathname === '/requirements';", 'Requirement Intelligence topbar mode');
requireText(topbar, "? 'muted'", 'Requirement Intelligence neutral topbar status');
requireText(topbar, "{isRequirementsPage ? '管理资料' : '开始验证'}", 'Requirement Intelligence topbar action');

console.log('requirement intelligence frontend contract passed');
