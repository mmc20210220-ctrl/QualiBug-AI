import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AgentHome } from './AgentHome';
import { Verify } from './Verify';
import { executeAgentTask, createAgentTask, getAgentTaskBundle, listAgentTasks, type AgentTask } from '../api/agent-tasks';

vi.mock('../api/agent-tasks', () => ({ executeAgentTask: vi.fn(), createAgentTask: vi.fn(), listAgentTasks: vi.fn(), getAgentTaskBundle: vi.fn(), groundAgentTask: vi.fn(), cancelAgentTask: vi.fn() }));
vi.mock('../api/data', () => ({ usePipelineData: () => ({ data: null, error: 'Project delivery unavailable', loading: false, refetch: vi.fn() }), isCustomerReadyFinding: () => false }));
vi.mock('../api/test-intelligence', () => ({ getTestIntelligence: vi.fn() }));
vi.mock('./EnterpriseCampaigns', () => ({ EnterpriseCampaigns: () => <div>Run control</div> }));
const task: AgentTask = { scanId: '', executionScope: '', executionSnapshotRef: '', executionClaimStatus: 'NOT_CLAIMED', executionLive: false, executionRecoveryRequired: false, executionCancelRequested: false, executionResult: {}, schemaVersion: 'qualibug.agent-task.v1', runtimeContext: {}, groundingEvaluatedAt: '', createdAt: '', completedAt: '', cancelledAt: '', eventCount: 1, latestEvent: null, taskId: 'task-a', projectId: 'workspace', goal: 'Check existing behavior', intent: 'verify_changes', status: 'BLOCKED', sourceSnapshotStatus: 'PINNED', sourceSnapshotRef: 'snapshot-a', sourceRevisionState: '', selectedTestTargets: [], selectedTargetSnapshots: [], executionRunId: '', runtimeGroundingStatus: 'BLOCKED', groundingBlockers: [{ code: 'ENV_REQUIRED', message: 'Declare test environment', source: 'preflight' }], groundingSummary: { selectedTargetCount: 0, runtimeBoundTargetCount: 0, preflightReady: false }, updatedAt: '2026-09-05T00:00:00Z' };

beforeEach(() => {
  vi.mocked(listAgentTasks).mockResolvedValue([task]);
  vi.mocked(createAgentTask).mockResolvedValue(task);
  vi.mocked(getAgentTaskBundle).mockResolvedValue({task, events: [{eventId: 'event-a', taskId: task.taskId, eventType: 'TASK_CREATED', occurredAt: '', schemaVersion: '', correlationId: '', detail: {}}]});
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it('keeps resumable tasks visible while project result fails', async () => {
  render(<MemoryRouter initialEntries={['/dashboard?project=workspace']}><AgentHome /></MemoryRouter>);
  const link = await screen.findByRole('link', { name: /Check existing behavior/ });
  expect(link.getAttribute('href')).toContain('task=task-a');
  expect(screen.getByText('Project delivery unavailable')).toBeTruthy();
  expect(vi.mocked(listAgentTasks)).toHaveBeenCalledWith('workspace');
});

it('suggestions fill a draft and create only on explicit submit', async () => {
  render(<MemoryRouter initialEntries={['/dashboard?project=workspace']}><Routes><Route path="/dashboard" element={<AgentHome />} /><Route path="/verify" element={<div>Persisted task workspace</div>} /></Routes></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', {name: '理解现有资料 ↗'}));
  expect(createAgentTask).not.toHaveBeenCalled();
  expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).not.toBe('');
  fireEvent.click(screen.getByRole('button', {name: '开始任务 ↗'}));
  await screen.findByText('Persisted task workspace');
  expect(createAgentTask).toHaveBeenCalledWith('workspace', expect.objectContaining({intent: 'analyze_requirements'}));
});

it('shows list failure without inventing an empty success state', async () => {
  vi.mocked(listAgentTasks).mockRejectedValue(new Error('Task service unavailable'));
  render(<MemoryRouter initialEntries={['/dashboard?project=workspace']}><AgentHome /></MemoryRouter>);
  await screen.findByText('Task service unavailable');
  expect(screen.queryByText('从一个问题开始')).toBeNull();
});

it('loads task ledger despite project delivery failure and preserves unknown counts', async () => {
  render(<MemoryRouter initialEntries={['/verify?project=workspace&task=task-a']}><Verify /></MemoryRouter>);
  await waitFor(() => expect(getAgentTaskBundle).toHaveBeenCalledWith('workspace', 'task-a'));
  await screen.findByText('Agent Task 已创建');
  expect(screen.getByRole('heading', { name: '补充实验需要的条件' })).toBeTruthy();
  expect(screen.getByText('Project delivery unavailable')).toBeTruthy();
  expect(screen.getByText('Project Findings · 未上报')).toBeTruthy();
  expect(screen.getByText('项目证据暂未读取')).toBeTruthy();
});

it('submits explicit project scope once and reloads durable state on network failure', async () => {
  let rejectExecution: (reason: Error) => void = () => {};
  vi.mocked(executeAgentTask).mockImplementation(() => new Promise((_resolve, reject) => { rejectExecution = reject; }));
  render(<MemoryRouter initialEntries={['/verify?project=workspace&task=task-a']}><Verify /></MemoryRouter>);
  const button = await screen.findByRole('button', {name: '按项目范围执行'});
  fireEvent.click(screen.getByRole('checkbox', {name: '仅执行只读实验'}));
  fireEvent.click(button);
  fireEvent.click(button);
  expect(executeAgentTask).toHaveBeenCalledTimes(1);
  expect(executeAgentTask).toHaveBeenCalledWith('workspace', 'task-a', true);
  vi.mocked(getAgentTaskBundle).mockResolvedValue({task: {...task, status: 'RUNNING', executionClaimStatus: 'CLAIMED', executionRecoveryRequired: true}, events: []});
  rejectExecution(new Error('Connection interrupted'));
  await screen.findByText('执行结果待确认');
  expect(screen.queryByRole('button', {name: '按项目范围执行'})).toBeNull();
  expect(screen.getByText('Connection interrupted')).toBeTruthy();
});

it('analysis tasks have no scan execution action', async () => {
  vi.mocked(getAgentTaskBundle).mockResolvedValue({task: {...task, intent: 'analyze_requirements'}, events: []});
  render(<MemoryRouter initialEntries={['/verify?project=workspace&task=task-a']}><Verify /></MemoryRouter>);
  await screen.findByRole('heading', {name: task.goal});
  expect(screen.queryByRole('button', {name: '按项目范围执行'})).toBeNull();
});
