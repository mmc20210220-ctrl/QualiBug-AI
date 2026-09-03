import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(process.cwd());
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const requireText = (content, expected, label) => assert(content.includes(expected), `${label}: missing ${expected}`);

const app = read('src/App.tsx');
const api = read('src/api/test-intelligence.ts');
const page = read('src/pages/TestIntelligence.tsx');
const layout = read('src/components/Layout.tsx');
const sidebar = read('src/components/Sidebar.tsx');
const topbar = read('src/components/Topbar.tsx');

for (const expected of [
  "import { TestIntelligence } from './pages/TestIntelligence';",
  'path="/test-intelligence" element={<TestIntelligence />}',
]) requireText(app, expected, 'Test Intelligence routing');

for (const expected of [
  '${API_V1_BASE}/projects/${encodeURIComponent(project)}/test-intelligence',
  "const ANALYSIS_SCHEMA = 'qualibug.test-intelligence.analysis.v1';",
  "const QUALITY_CLAIM = 'DETERMINISTIC_SUPPORTED_SEMANTIC_OBLIGATION_COVERAGE_NOT_TOTAL_TEST_COMPLETENESS';",
  "requireExact(coverage.execution_coverage_status, 'NOT_MEASURED'",
  "requireExact(row.design_status, 'OBLIGATION_ONLY'",
  "requireExact(row.verification_status, 'NOT_MEASURED'",
  "requireExact(row.runtime_linkage, 'NOT_EVALUATED'",
  'requirementFindingIds: asStringArray(row.requirement_finding_ids)',
  "throw contractError('coverage.supported_semantic_unit_counts')",
]) requireText(api, expected, 'Test Intelligence API truth contract');

for (const expected of [
  'Supported Semantic Coverage',
  '不是总测试完整率',
  '仅义务',
  '未执行',
  'OBLIGATION_ONLY / NOT_MEASURED / NOT_EVALUATED',
  '系统不会把空集合显示成 100% 覆盖',
  '必须验证的业务语义',
  '关联需求审查项',
  '只做可证明的精确关联',
  '相似文本、同来源或邻近业务语义不会自动绑定',
]) requireText(page, expected, 'Test Intelligence workspace');

for (const expected of [
  "location.pathname === '/test-intelligence'",
  '{!isIntelligenceWorkspace && <RunCustomerResultSummary />}',
  '{!isIntelligenceWorkspace && <RunLifecycleBanner />}',
]) requireText(layout, expected, 'Test Intelligence runtime isolation');

requireText(sidebar, "{ to: 'test-intelligence', icon: 'test-intelligence', label: '测试智能' }", 'Test Intelligence primary navigation');
for (const expected of [
  "'/test-intelligence': '测试智能'",
  "const isTestIntelligencePage = location.pathname === '/test-intelligence';",
  "? '测试智能模式'",
  "? '证据化测试义务与支持语义覆盖'",
]) requireText(topbar, expected, 'Test Intelligence topbar mode');

console.log('test intelligence frontend contract passed');
