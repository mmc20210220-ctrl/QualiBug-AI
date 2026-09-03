import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(process.cwd());
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const requireText = (content, expected, label) => assert(content.includes(expected), `${label}: missing ${expected}`);

const app = read('src/App.tsx');
const api = read('src/api/test-intelligence.ts');
const page = read('src/pages/TestIntelligence.tsx');
const main = read('src/main.tsx');
const designCss = read('src/pages/TestDesign.css');
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
  "const TEST_DESIGN_SCHEMA = 'qualibug.test-design.v1';",
  "const TEST_DESIGN_PROJECTION_SCHEMA = 'qualibug.test-design-projection.v1';",
  "const TEST_DESIGN_QUALITY_CLAIM = 'DETERMINISTIC_OBLIGATION_DERIVED_TEST_DESIGN_NOT_RUNTIME_GROUNDING_OR_EXECUTION';",
  "requireExact(coverage.execution_coverage_status, 'NOT_MEASURED'",
  "requireExact(row.design_status, 'OBLIGATION_ONLY'",
  "requireExact(row.verification_status, 'NOT_MEASURED'",
  "requireExact(row.runtime_linkage, 'NOT_EVALUATED'",
  "requireExact(row.design_status, 'STRUCTURED_DESIGN_ONLY'",
  "requireExact(action.execution_surface, 'NOT_SELECTED'",
  "requireExact(action.binding_status, 'NOT_GROUNDED'",
  "requireExact(row.execution_status, 'NOT_EXECUTED'",
  "requireExact(designProjection.runtime_grounding_status, 'NOT_GROUNDED'",
  "requireExact(designProjection.runtime_execution_status, 'NOT_EXECUTED'",
  'requirementFindingIds: asStringArray(row.requirement_finding_ids)',
  "throw contractError('summary.requirement_finding_linked_obligation_count/obligations')",
  "throw contractError('summary.requirement_finding_linked_design_count/test_designs')",
  "throw contractError('test_design_projection.eligible_obligation_count/obligations')",
  "throw contractError('coverage.supported_semantic_unit_counts')",
]) requireText(api, expected, 'Test Intelligence API truth contract');

for (const expected of [
  'Supported Semantic Coverage',
  '不是总测试完整率',
  '仅义务',
  '未执行',
  'STRUCTURED_DESIGN_ONLY / NOT_GROUNDED / NOT_EXECUTED',
  '不会生成 API 路径、UI 点击步骤、测试账号或具体数据值',
  '从“必须验证什么”到“如何验证”',
  '关联需求审查项',
  '仅展示后端已证明的精确关联',
  '系统不会把空集合显示成 100% 覆盖',
]) requireText(page, expected, 'Test Intelligence workspace');

requireText(main, "import './pages/TestDesign.css';", 'Test Design CSS registration');
for (const expected of ['.ti-design', '.ti-design-grid', '.ti-design-status']) {
  requireText(designCss, expected, 'Test Design presentation');
}

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