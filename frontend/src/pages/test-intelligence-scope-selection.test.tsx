import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TestIntelligence } from './TestIntelligence';
import { getAgentTask, groundAgentTask, type AgentTask } from '../api/agent-tasks';
import { getTestIntelligence, type TestIntelligenceAnalysis } from '../api/test-intelligence';

const navigateToProjectPath = vi.fn();

vi.mock('../api/agent-tasks', () => ({
  getAgentTask: vi.fn(),
  groundAgentTask: vi.fn(),
}));
vi.mock('../api/test-intelligence', () => ({ getTestIntelligence: vi.fn() }));
vi.mock('../lib/project-navigation', () => ({
  useProjectNavigation: () => ({ navigateToProjectPath }),
}));

const task = {
  taskId: 'task-a',
  projectId: 'workspace',
  goal: 'Check changed behavior',
  intent: 'verify_changes',
  status: 'BLOCKED',
  executionClaimStatus: 'NOT_CLAIMED',
  sourceSnapshotStatus: 'PINNED',
  selectedTestTargets: [],
  groundingBlockers: [{ code: 'CHANGE_SCOPE_NOT_GROUNDED', message: 'scope required', source: 'agent_task' }],
} as unknown as AgentTask;

const analysis = {
  coverage: {
    status: 'COVERED',
    eligibleSupportedSemanticUnitCount: 1,
    obligatedSupportedSemanticUnitCount: 1,
    uncoveredSupportedSemanticUnitCount: 0,
    uncoveredSupportedSemanticUnitIds: [],
    countsByObligationKind: { business_rule: 0, lifecycle_transition: 0, authorization: 1, side_effect: 0, requirement_risk: 0 },
    executionCoverageStatus: 'NOT_MEASURED',
  },
  summary: {
    sourceCount: 1,
    obligationCount: 1,
    testDesignCount: 1,
    requirementFindingLinkedDesignCount: 0,
    suppressedWithoutEvidenceCount: 0,
    unsupportedFormalBehaviorCount: 0,
    undesignedObligationCount: 0,
  },
  testDesignProjection: { status: 'DESIGNED', undesignedObligationIds: [] },
  obligations: [{
    obligationId: 'target-a',
    obligationKind: 'authorization',
    title: 'Check changed behavior',
    objective: 'Verify the source-backed behavior.',
    actorRefs: [],
    objectRefs: [],
    operationRef: '',
    preconditions: [],
    expectedOutcomes: [{ kind: 'authorization_decision', decision: 'ALLOW' }],
    businessConstraints: [],
    sourceRefs: [],
    sourceIds: ['source-a'],
    evidence: [{ sourceId: 'source-a', sourceLocator: 'doc#1', quote: 'quoted source' }],
    requirementFindingIds: [],
    designStatus: 'OBLIGATION_ONLY',
    verificationStatus: 'NOT_MEASURED',
    runtimeLinkage: 'NOT_EVALUATED',
    riskLevel: 'NOT_ASSESSED',
  }],
  testDesigns: [],
} as unknown as TestIntelligenceAnalysis;

beforeEach(() => {
  vi.mocked(getAgentTask).mockResolvedValue(task);
  vi.mocked(getTestIntelligence).mockResolvedValue(analysis);
  vi.mocked(groundAgentTask).mockResolvedValue({ ...task, selectedTestTargets: ['target-a'] });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('Test Intelligence task scope selection', () => {
  it('requires explicit target confirmation before grounding a verify task', async () => {
    render(<MemoryRouter initialEntries={['/analyze?project=workspace&view=test-targets&task=task-a']}><TestIntelligence /></MemoryRouter>);

    await screen.findByText('选择这次任务关注的验证目标');
    const submit = screen.getByRole('button', { name: '固定 0 个目标并重新评估' });
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole('checkbox', { name: '选择 Test Target：Check changed behavior' }));
    expect((screen.getByRole('button', { name: '固定 1 个目标并重新评估' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('checkbox', { name: '将所选目标保存为本次任务关注范围' }));
    fireEvent.click(screen.getByRole('button', { name: '固定 1 个目标并重新评估' }));

    await waitFor(() => expect(groundAgentTask).toHaveBeenCalledWith('workspace', 'task-a', { testTargetIds: ['target-a'] }));
    expect(navigateToProjectPath).toHaveBeenCalledWith('/verify', 'workspace', 'task=task-a');
  });

  it('keeps scope selection unavailable when the pinned Snapshot is stale', async () => {
    vi.mocked(getAgentTask).mockResolvedValue({ ...task, sourceSnapshotStatus: 'PINNED_STALE' });
    render(<MemoryRouter initialEntries={['/analyze?project=workspace&view=test-targets&task=task-a']}><TestIntelligence /></MemoryRouter>);

    await screen.findByText('当前 Task 暂不能选择 Test Targets');
    expect(screen.queryByRole('checkbox', { name: /选择 Test Target/ })).toBeNull();
  });
});


it('does not allow target selection after execution has been claimed', async () => {
  vi.mocked(getAgentTask).mockResolvedValue({ ...task, status: 'RUNNING', executionClaimStatus: 'CLAIMED' });
  render(<MemoryRouter initialEntries={['/analyze?project=workspace&view=test-targets&task=task-a']}><TestIntelligence /></MemoryRouter>);
  await screen.findByText('当前 Task 暂不能选择 Test Targets');
  expect(screen.queryByRole('checkbox', { name: /选择 Test Target/ })).toBeNull();
  expect(groundAgentTask).not.toHaveBeenCalled();
});

it('retains selected targets across pagination and search without executing grounding', async () => {
  vi.mocked(getTestIntelligence).mockResolvedValue({ ...analysis, obligations: Array.from({ length: 25 }, (_, i) => ({ ...analysis.obligations[0], obligationId: `target-${i}`, title: `Scenario ${i}` })) });
  vi.mocked(getAgentTask).mockResolvedValue(task);
  render(<MemoryRouter initialEntries={['/analyze?project=workspace&task=task-a']}><TestIntelligence /></MemoryRouter>);
  const selected = await screen.findByRole('checkbox', { name: '选择 Test Target：Scenario 0' });
  fireEvent.click(selected);
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  expect(screen.queryByRole('heading', { name: 'Scenario 0' })).toBeNull();
  expect(screen.getByRole('heading', { name: 'Scenario 24' })).toBeTruthy();
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Scenario 0' } });
  expect((screen.getByRole('checkbox', { name: '选择 Test Target：Scenario 0' }) as HTMLInputElement).checked).toBe(true);
  expect(groundAgentTask).not.toHaveBeenCalled();
  cleanup();
});

it('presents source data requirements separately from materialized test data', async () => {
  vi.mocked(getTestIntelligence).mockResolvedValue({
    ...analysis,
    testDesigns: [{
      designId: 'design-data-a',
      title: '验证订单取消后的状态变化',
      setup: {
        objectRefs: ['订单'],
        actorRefs: ['普通用户'],
        preconditions: ['订单已进入可取消状态'],
        testDataRequirements: ['订单状态满足来源前置条件'],
        testDataMaterializationStatus: 'NOT_MATERIALIZED',
        environmentStatus: 'NOT_SELECTED',
      },
      sourceIds: ['prd.md'],
      evidence: [{ sourceId: 'prd.md' }],
    }] as unknown as TestIntelligenceAnalysis['testDesigns'],
  });
  render(<MemoryRouter initialEntries={['/analyze?project=workspace&view=test-data']}><TestIntelligence /></MemoryRouter>);
  await screen.findByRole('heading', { name: '测试数据准备' });
  expect(screen.getByText('来源约束条目')).toBeTruthy();
  expect(screen.getByText(/数据准备尚未完成/)).toBeTruthy();
  expect(screen.getByText('已准备数据的设计')).toBeTruthy();
  expect(screen.getAllByText('0').length).toBeGreaterThan(0);
  expect(screen.getByText('验证订单取消后的状态变化')).toBeTruthy();
  expect(screen.getByText('订单状态满足来源前置条件')).toBeTruthy();
  expect(screen.queryByText('测试账号')).toBeNull();
  fireEvent.change(screen.getByRole('combobox', { name: '数据要求' }), { target: { value: 'undeclared' } });
  expect(screen.getByText('没有匹配的数据要求')).toBeTruthy();
  expect(screen.queryByText('验证订单取消后的状态变化')).toBeNull();
  expect(screen.getByText('已准备数据的设计')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '清除数据筛选' }));
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索数据要求' }), { target: { value: 'prd.md' } });
  expect(screen.getByText('验证订单取消后的状态变化')).toBeTruthy();
  expect(groundAgentTask).not.toHaveBeenCalled();
  cleanup();
});
