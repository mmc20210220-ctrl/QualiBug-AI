import { readFile } from 'node:fs/promises';
import process from 'node:process';

const root = new URL('../', import.meta.url);

async function source(path) {
  return (await readFile(new URL(path, root), 'utf8')).replace(/\r\n/g, '\n');
}

function requireAll(content, expected, label) {
  for (const value of expected) {
    if (!content.includes(value)) {
      throw new Error(`${label}: missing Agent Workspace contract text: ${value}`);
    }
  }
}

function forbidAll(content, forbidden, label) {
  for (const value of forbidden) {
    if (content.includes(value)) {
      throw new Error(`${label}: forbidden Agent Workspace implementation returned: ${value}`);
    }
  }
}

const [app, sidebar, home, verify, findings, decision, analyze, frontendAgents] = await Promise.all([
  source('src/App.tsx'),
  source('src/components/Sidebar.tsx'),
  source('src/pages/AgentHome.tsx'),
  source('src/pages/Verify.tsx'),
  source('src/pages/AgentFindings.tsx'),
  source('src/pages/AgentDecision.tsx'),
  source('src/pages/Analyze.tsx'),
  source('AGENTS.md'),
]);

requireAll(app, [
  '<Route path="/dashboard" element={<AgentHome />} />',
  '<Route path="/verify" element={<Verify />} />',
  '<Route path="/findings" element={<AgentFindings />} />',
  '<Route path="/release" element={<AgentDecision />} />',
  '<Route path="/advanced-dashboard" element={<Dashboard />} />',
  '<Route path="/advanced-findings" element={<Findings />} />',
  '<Route path="/release/details" element={<ReleaseGate />} />',
], 'App routes');

requireAll(sidebar, [
  "label: '新任务'",
  "label: '工作台'",
  "label: 'Findings'",
  "label: 'Decision'",
  "label: 'Knowledge'",
  "label: 'Sources'",
  'AI Quality Engineer',
], 'Agent navigation');

requireAll(home, [
  '今天要我帮你验证什么？',
  '真实执行范围仍由已连接资料、运行环境和 Preflight 决定',
  "navigateToProjectPath(mode === 'analyze' ? '/analyze' : '/verify'",
  '没有已确认 Finding 不等于系统安全',
], 'Agent Home');
forbidAll(home, ['Math.random(', 'setInterval('], 'Agent Home');

requireAll(verify, [
  "type AgentMilestone =",
  "label: 'Understanding'",
  "label: 'Planning'",
  "label: 'Acting'",
  "label: 'Observing'",
  "label: 'Evaluating'",
  "label: 'Finding'",
  '任务文本不会绕过资料 authority、Preflight 或执行安全边界',
  '统一逐步骤 Agent Run / Live Surface 尚未上报',
  '当前工作台不会用静态示意图冒充 Browser Live View',
  '<EnterpriseCampaigns />',
], 'Live Workspace');
forbidAll(verify, ['Math.random(', 'fakeBrowser', 'mockAgent'], 'Live Workspace');

requireAll(findings, [
  'isCustomerReadyFinding',
  '我发现 ${p0Count} 个会阻断发布的问题',
  '列表为空或没有 P0 都不能单独解释为系统安全',
  '<FindingCard',
  '<EvidenceDrawer',
], 'Agent Findings');

requireAll(decision, [
  'deriveReleasePresentation',
  '我建议暂缓这个版本的发布',
  '当前证据支持发布这个版本',
  '目前没有足够的项目级放行证据，QualiBug 保持保守结论',
  "to={buildProjectPath('/release/details', project)}",
], 'Agent Decision');
forbidAll(decision, ['Math.random(', 'releaseScore =', 'riskScore ='], 'Agent Decision');

requireAll(analyze, [
  'Knowledge · Understanding',
  '该文本只作为当前工作目标上下文',
  '需求真值与 Test Targets 仍只来自已接入资料和后端 Intelligence API',
  '<RequirementIntelligence',
  '<TestIntelligence',
], 'Knowledge surface');

requireAll(frontendAgents, [
  '**Agent-first**',
  'Free-text goal input is task context only',
  'These are explainable work states, not hidden chain-of-thought',
  'must never fabricate Agent steps, browser previews, execution surfaces, runtime grounding, logs or observations',
], 'Frontend SSOT');

process.stdout.write('agent workspace contract: OK\n');
