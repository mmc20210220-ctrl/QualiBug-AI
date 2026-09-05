import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  vi.clearAllMocks();
});

describe('Test Intelligence task scope selection', () => {
  it('requires explicit target confirmation before grounding a verify task', async () => {
    render(<MemoryRouter initialEntries={['/analyze?project=workspace&view=test-targets&task=task-a']}><TestIntelligence /></MemoryRouter>);

    await screen.findByText('为这次任务选择真实变更影响范围');
    const submit = screen.getByRole('button', { name: '固定 0 个目标并重新评估' });
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole('checkbox', { name: '选择 Test Target：Check changed behavior' }));
    expect((screen.getByRole('button', { name: '固定 1 个目标并重新评估' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('checkbox', { name: '我确认下面选择的目标来自本次真实变更范围' }));
    fireEvent.click(screen.getByRole('button', { name: '固定 1 个目标并重新评估' }));

    await waitFor(() => expect(groundAgentTask).toHaveBeenCalledWith('workspace', 'task-a', { testTargetIds: ['target-a'] }));
    expect(navigateToProjectPath).toHaveBeenCalledWith('/verify', 'workspace', 'task=task-a');
  });
});
