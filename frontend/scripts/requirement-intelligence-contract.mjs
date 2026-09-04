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
const analyze = read('src/pages/Analyze.tsx');
const api = read('src/api/requirement-intelligence.ts');
const page = read('src/pages/RequirementIntelligence.tsx');
const layout = read('src/components/Layout.tsx');
const sidebar = read('src/components/Sidebar.tsx');
const topbar = read('src/components/Topbar.tsx');

for (const expected of [
  "import { Analyze } from './pages/Analyze';",
  'path="/analyze" element={<Analyze />}',
  'path="/requirements" element={<RequirementIntelligence />}',
  'path="/products" element={<PreserveSearchRedirect to="/analyze" />}',
]) requireText(app, expected, 'Analyze / Requirement Intelligence routing');
requireText(app, 'path="*" element={<PreserveSearchRedirect to="/dashboard" />}', 'legacy route compatibility');

for (const expected of [
  "type AnalyzeView = 'requirements' | 'test-targets';",
  '<RequirementIntelligence />',
  '<TestIntelligence />',
  '管理资料来源',
  '先理解软件应该如何工作，再决定需要验证什么',
]) requireText(analyze, expected, 'Unified Analyze workspace');

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
  "const isFocusedWorkspace = location.pathname === '/analyze'",
  "location.pathname === '/verify'",
  "location.pathname === '/requirements'",
  "location.pathname === '/test-intelligence'",
  '{!isFocusedWorkspace && <RunCustomerResultSummary />}',
  '{!isFocusedWorkspace && <RunLifecycleBanner />}',
]) requireText(layout, expected, 'Analyze / Verify runtime isolation');

for (const expected of [
  "{ to: 'analyze', icon: 'analyze', label: '分析' }",
  "{ to: 'verify', icon: 'verify', label: '验证' }",
]) requireText(sidebar, expected, 'AI-native primary navigation');

for (const expected of [
  "'/analyze': '分析'",
  "const isAnalyzePage = location.pathname === '/analyze';",
  "const isIntelligencePage = isAnalyzePage || isRequirementsPage || isTestIntelligencePage;",
  "? '分析模式'",
  "? '需求审查、业务语义与验证目标'",
  "? { path: '/materials', search: '', label: '管理资料' }",
]) requireText(topbar, expected, 'Analyze topbar mode');

console.log('requirement intelligence frontend contract passed');
