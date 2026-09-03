// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
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
    getRequirementIntelligenceMock.mockReset();
    getRequirementIntelligenceMock.mockResolvedValue(ANALYSIS);
  });

  it('renders readiness, evidence and bounded quality wording', async () => {
    render(
      <MemoryRouter initialEntries={['/requirements?project=project-a']}>
        <RequirementIntelligence />
      </MemoryRouter>,
    );

    expect(await screen.findByText('NOT_READY')).toBeTruthy();
    expect(screen.getByText('业务规则约束冲突')).toBeTruthy();
    expect(screen.getByText('已支付订单不得取消')).toBeTruthy();
    expect(screen.getByText(/不等于资料完整率或问题召回率为 100%/)).toBeTruthy();
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
});
