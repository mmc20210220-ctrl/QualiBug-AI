// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RequirementIntelligenceAnalysis } from '../../api/requirement-intelligence';

const { getRequirementIntelligenceMock } = vi.hoisted(() => ({
  getRequirementIntelligenceMock: vi.fn(),
}));

vi.mock('../../api/requirement-intelligence', async () => {
  const actual = await vi.importActual<typeof import('../../api/requirement-intelligence')>('../../api/requirement-intelligence');
  return {
    ...actual,
    getRequirementIntelligence: (...args: unknown[]) => getRequirementIntelligenceMock(...args),
  };
});

import { RequirementIntelligence } from '../RequirementIntelligence';

const ANALYSIS: RequirementIntelligenceAnalysis = {
  schema: 'qualibug.requirement-intelligence.analysis.v1',
  productId: 'requirement_intelligence',
  projectId: 'project-a',
  analysisStatus: 'NOT_READY',
  summary: {
    sourceCount: 4,
    conflictCount: 1,
    missingCount: 1,
    ambiguityCount: 0,
    blockingFindingCount: 1,
    reviewRequiredFindingCount: 1,
    suppressedWithoutEvidenceCount: 0,
  },
  readiness: {
    status: 'NOT_READY',
    ready: false,
    findingCount: 2,
    blockingFindingCount: 1,
    reviewRequiredFindingCount: 1,
    blockingFindingIds: ['requirement:c1'],
    reviewRequiredFindingIds: ['requirement:m1'],
    countsByType: {
      requirement_conflict: 1,
      requirement_missing: 1,
      requirement_ambiguity: 0,
    },
    qualityClaim: 'DETERMINISTIC_FINDING_GATE_NOT_COMPLETENESS_OR_RECALL',
  },
  findings: [
    {
      findingId: 'requirement:c1',
      findingType: 'requirement_conflict',
      title: '业务规则约束冲突',
      description: '取消订单规则在 PRD 和 API 中不一致。',
      status: 'open',
      severity: '',
      blocking: true,
      sourceIds: ['prd', 'api'],
      evidence: [
        {
          sourceId: 'prd',
          sourceLocator: 'PRD.md#line=20',
          assetRef: '',
          documentBlockId: '',
          documentNodeId: '',
          quote: '已支付订单不得取消',
          quoteHash: '',
          factId: 'fact:prd',
          derivation: 'source_span',
        },
      ],
      operatorAction: '请选择被批准的来源版本。',
      relatedObjectRefs: [],
      relatedOperationRefs: [],
      reviewStatus: '',
      reasonCode: 'BUSINESS_MODALITY_CONTRADICTION',
    },
    {
      findingId: 'requirement:m1',
      findingType: 'requirement_missing',
      title: '生命周期起始状态定义缺失',
      description: '订单取消前的起始状态未定义。',
      status: 'open',
      severity: 'P1',
      blocking: false,
      sourceIds: ['state'],
      evidence: [
        {
          sourceId: 'state',
          sourceLocator: 'state.md#line=8',
          assetRef: '',
          documentBlockId: '',
          documentNodeId: '',
          quote: '取消后进入 CANCELLED',
          quoteHash: '',
          factId: 'fact:state',
          derivation: 'source_span',
        },
      ],
      operatorAction: '请补充起始状态。',
      relatedObjectRefs: ['订单'],
      relatedOperationRefs: ['取消'],
      reviewStatus: '',
      reasonCode: 'LIFECYCLE_FROM_STATE_UNKNOWN',
    },
  ],
};

describe('Requirement Intelligence workspace', () => {
  beforeEach(() => {
    HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    HTMLDialogElement.prototype.close = function () { this.open = false; };
    getRequirementIntelligenceMock.mockReset();
    getRequirementIntelligenceMock.mockResolvedValue(ANALYSIS);
  });

  afterEach(() => {
    cleanup();
  });

  it('renders readiness, evidence and bounded quality wording', async () => {
    render(
      <MemoryRouter initialEntries={['/requirements?project=project-a']}>
        <RequirementIntelligence />
      </MemoryRouter>,
    );

    expect(await screen.findByText('需求就绪状态 · 未就绪')).toBeTruthy();
    expect(screen.getByText('业务规则约束冲突')).toBeTruthy();
    expect(screen.queryByText('已支付订单不得取消')).toBeNull();
    const trigger = screen.getByRole('button', { name: '业务规则约束冲突' });
    trigger.focus();
    fireEvent.click(trigger);
    expect(screen.getByRole('dialog', { name: '需求审查详情' })).toBeTruthy();
    expect(screen.getByText('已支付订单不得取消')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '关闭详情' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    const scope = screen.getByText('审查范围与状态说明').closest('details');
    expect(scope?.open).toBe(false);
    expect(screen.getByText(/不代表资料完整或已执行测试/)).toBeTruthy();
    expect(getRequirementIntelligenceMock).toHaveBeenCalledWith('project-a');
  });

  it('filters findings by the three supported product categories', async () => {
    render(
      <MemoryRouter initialEntries={['/requirements?project=project-a']}>
        <RequirementIntelligence />
      </MemoryRouter>,
    );

    await screen.findByText('业务规则约束冲突');
    fireEvent.click(screen.getByRole('button', { name: '缺失 1' }));

    expect(screen.queryByText('业务规则约束冲突')).toBeNull();
    expect(screen.getByText('生命周期起始状态定义缺失')).toBeTruthy();
  });

  it('does not call the analysis API when no project is selected', () => {
    render(
      <MemoryRouter initialEntries={['/requirements']}>
        <RequirementIntelligence />
      </MemoryRouter>,
    );

    expect(screen.getByText('先选择项目，再开始需求审查')).toBeTruthy();
    expect(getRequirementIntelligenceMock).not.toHaveBeenCalled();
  });

  it('bounds large finding responses to a page while keeping every item reachable', async () => {
    const findings = Array.from({ length: 45 }, (_, index) => ({
      ...ANALYSIS.findings[0],
      findingId: `requirement:c${index}`,
      title: `冲突 ${index}`,
    }));
    getRequirementIntelligenceMock.mockResolvedValue({
      ...ANALYSIS,
      findings,
      readiness: {
        ...ANALYSIS.readiness,
        findingCount: findings.length,
        blockingFindingCount: findings.length,
        reviewRequiredFindingCount: 0,
        blockingFindingIds: findings.map((finding) => finding.findingId),
        reviewRequiredFindingIds: [],
        countsByType: { requirement_conflict: findings.length, requirement_missing: 0, requirement_ambiguity: 0 },
      },
    });

    render(
      <MemoryRouter initialEntries={['/requirements?project=project-a']}>
        <RequirementIntelligence />
      </MemoryRouter>,
    );

    expect(await screen.findByText('第 1 / 3 页')).toBeTruthy();
    expect(screen.getByText('当前显示 1–20 / 45 条')).toBeTruthy();
    expect(screen.getByText('冲突 0')).toBeTruthy();
    expect(screen.queryByText('冲突 20')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getByText('第 2 / 3 页')).toBeTruthy();
    expect(screen.getByText('冲突 20')).toBeTruthy();
  });
});

it('combines search and understanding filters without changing source readiness', async () => {
  getRequirementIntelligenceMock.mockResolvedValue(ANALYSIS);
  const { container } = render(<MemoryRouter initialEntries={['/requirements?project=project-a']}><RequirementIntelligence /></MemoryRouter>);
  await screen.findByText('业务规则约束冲突');
  expect(container.querySelectorAll('details[open]').length).toBe(0);
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'state' } });
  expect(screen.queryByText('业务规则约束冲突')).toBeNull();
  expect(screen.getByText('生命周期起始状态定义缺失')).toBeTruthy();
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByText('没有匹配当前筛选的审查项')).toBeTruthy();
  expect(screen.getByText('需求就绪状态 · 未就绪')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '清除筛选' }));
  expect(screen.getByText('业务规则约束冲突')).toBeTruthy();
  cleanup();
});
