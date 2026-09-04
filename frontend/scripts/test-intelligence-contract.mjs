import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(process.cwd());
const read = (relative) => fs.readFileSync(path.join(root, relative), 'utf8').replace(/\r\n/g, '\n');
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const requireText = (content, expected, label) => assert(content.includes(expected), `${label}: missing ${expected}`);

const app = read('src/App.tsx');
const analyze = read('src/pages/Analyze.tsx');
const verify = read('src/pages/Verify.tsx');
const api = read('src/api/test-intelligence.ts');
const page = read('src/pages/TestIntelligence.tsx');
const designCss = read('src/pages/TestDesign.css');
const layout = read('src/components/Layout.tsx');
const sidebar = read('src/components/Sidebar.tsx');
const topbar = read('src/components/Topbar.tsx');

for (const expected of [
  "import { Analyze } from './pages/Analyze';",
  "import { Verify } from './pages/Verify';",
  'path="/analyze" element={<Analyze />}',
  'path="/verify" element={<Verify />}',
  'path="/test-intelligence" element={<TestIntelligence />}',
]) requireText(app, expected, 'Analyze / Verify / Test Intelligence routing');

for (const expected of [
  "type AnalyzeView = 'requirements' | 'test-targets';",
  '<TestIntelligence',
  '<strong>Test Targets</strong>',
  "await ingestKnowledge(project, file, 'prd');",
]) requireText(analyze, expected, 'Unified Analyze Test Targets surface');

for (const expected of [
  'getTestIntelligence',
  '<strong>Test Targets</strong>',
  '<strong>Agent Execution</strong>',
  '<strong>Execution Surface</strong>',
  '逐步骤 Agent Run 事件流尚未接入这个统一工作台',
  '不会拿静态示意图冒充 Live View',
  '<EnterpriseCampaigns />',
]) requireText(verify, expected, 'Verify honesty and run-control boundary');

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
  "import './TestDesign.css';",
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

for (const expected of ['.ti-design', '.ti-design-grid', '.ti-design-status']) {
  requireText(designCss, expected, 'Test Design presentation');
}

for (const expected of [
  "const isFocusedWorkspace = location.pathname === '/analyze'",
  "location.pathname === '/verify'",
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
  "'/verify': '验证'",
  "const isAnalyzePage = location.pathname === '/analyze';",
  "const isVerifyPage = location.pathname === '/verify';",
  "? '需求审查、业务语义与验证目标'",
  "? '真实运行、证据与验证闭环'",
]) requireText(topbar, expected, 'Analyze / Verify topbar mode');

console.log('test intelligence frontend contract passed');
