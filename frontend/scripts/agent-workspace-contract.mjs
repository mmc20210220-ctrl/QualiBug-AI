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

const [app, sidebar, home, verify, agentTasks, findings, decision, analyze, frontendAgents] = await Promise.all([
  source('src/App.tsx'),
  source('src/components/Sidebar.tsx'),
  source('src/pages/AgentHome.tsx'),
  source('src/pages/Verify.tsx'),
  source('src/api/agent-tasks.ts'),
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

requireAll(agentTasks, [
  'export type AgentTaskIntent =',
  'export type AgentTaskStatus =',
  'export type AgentGroundingBlocker =',
  'export type AgentPinnedTestTarget =',
  'export async function createAgentTask(',
  'export async function groundAgentTask(',
  'export async function getAgentTaskBundle(',
  'export async function cancelAgentTask(',
  '/projects/${encodeURIComponent(project)}/agent-tasks',
  '/${encodeURIComponent(taskId)}/ground',
  'selected_test_target_snapshot',
], 'Agent Task API');

requireAll(home, [
  '今天要我帮你验证什么？',
  'createAgentTask(project',
  "next.set('task', task.taskId);",
  'Goal 是任务上下文，不是执行授权',
  'Runtime Grounding 和 Preflight',
  '没有已确认 Finding 不等于系统安全',
], 'Agent Home');
forbidAll(home, ['Math.random(', 'setInterval('], 'Agent Home');

requireAll(verify, [
  'type AgentMilestone =',
  "label: 'Task'",
  "label: 'Understanding'",
  "label: 'Planning'",
  "label: 'Grounding'",
  "label: 'Acting'",
  "label: 'Observing'",
  "label: 'Evaluating'",
  "label: 'Finding'",
  'if (!project || taskId)',
  'getAgentTaskBundle(project, taskId)',
  'groundAgentTask(project, taskId)',
  'Agent Event Ledger',
  'UNDERSTANDING_SNAPSHOT_PINNED',
  'RUNTIME_GROUNDING_EVALUATED',
  '该 Task 固定的 Test Targets',
  'Runtime Grounding 被真实条件阻断',
  '尚未绑定 execution_run_id；现有 Campaign 状态不会冒充 Task 执行事件',
  'Runtime Grounding 已就绪，尚未开始 Task-specific Execution',
  '前端不会用模拟日志填充 Event Ledger',
  '<EnterpriseCampaigns />',
], 'Live Workspace');
forbidAll(verify, [
  'Math.random(',
  'fakeBrowser',
  'mockAgent',
], 'Live Workspace');

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
  'creates a real project-scoped backend Agent Task',
  'read-only grounding evaluation',
  'Task events are observable work events, not hidden chain-of-thought',
  'frontend must never synthesize missing Agent events',
  'Agent Task grounding is not execution',
  'cancels only the orchestration record',
], 'Frontend SSOT');

process.stdout.write('agent workspace contract: OK\n');
