import { describe, expect, it } from 'vitest';
import { parseRequirementIntelligenceAnalysis } from '../requirement-intelligence';

describe('Requirement Intelligence frontend projection', () => {
  it('preserves readiness truth and source evidence', () => {
    const analysis = parseRequirementIntelligenceAnalysis({
      ok: true,
      data: {
        schema: 'qualibug.requirement-intelligence.analysis.v1',
        product_id: 'requirement_intelligence',
        project_id: 'p1',
        analysis_status: 'NOT_READY',
        summary: {
          source_count: 3,
          requirement_conflict_count: 1,
          requirement_missing_count: 0,
          requirement_ambiguity_count: 0,
          blocking_finding_count: 1,
          review_required_finding_count: 0,
          suppressed_without_evidence_count: 0,
        },
        readiness: {
          status: 'NOT_READY',
          ready: false,
          finding_count: 1,
          blocking_finding_count: 1,
          review_required_finding_count: 0,
          blocking_finding_ids: ['requirement:c1'],
          review_required_finding_ids: [],
          counts_by_type: {
            requirement_conflict: 1,
            requirement_missing: 0,
            requirement_ambiguity: 0,
          },
          quality_claim: 'DETERMINISTIC_FINDING_GATE_NOT_COMPLETENESS_OR_RECALL',
        },
        findings: [
          {
            finding_id: 'requirement:c1',
            finding_type: 'requirement_conflict',
            title: '业务规则约束冲突',
            description: 'PRD 与 API 对取消条件定义不一致。',
            blocking: true,
            source_ids: ['prd', 'api'],
            evidence: [
              {
                source_id: 'prd',
                source_locator: 'PRD.md#line=20',
                quote: '已支付订单不得取消',
                fact_id: 'fact:prd',
              },
            ],
          },
        ],
      },
    });

    expect(analysis.readiness.status).toBe('NOT_READY');
    expect(analysis.readiness.qualityClaim).toBe('DETERMINISTIC_FINDING_GATE_NOT_COMPLETENESS_OR_RECALL');
    expect(analysis.findings).toHaveLength(1);
    expect(analysis.findings[0].findingType).toBe('requirement_conflict');
    expect(analysis.findings[0].evidence[0]).toMatchObject({
      sourceId: 'prd',
      sourceLocator: 'PRD.md#line=20',
      quote: '已支付订单不得取消',
      factId: 'fact:prd',
    });
  });

  it('fails closed on unknown readiness instead of inventing a healthy fallback', () => {
    expect(() => parseRequirementIntelligenceAnalysis({
      data: {
        analysis_status: 'NEW_SERVER_STATUS',
        readiness: {
          status: 'NEW_SERVER_STATUS',
        },
      },
    })).toThrow(/readiness.status/);
  });

  it('fails closed on unknown finding types instead of relabelling them', () => {
    expect(() => parseRequirementIntelligenceAnalysis({
      data: {
        schema: 'qualibug.requirement-intelligence.analysis.v1',
        product_id: 'requirement_intelligence',
        project_id: 'p1',
        analysis_status: 'NOT_READY',
        summary: {
          source_count: 1,
          requirement_conflict_count: 1,
          requirement_missing_count: 0,
          requirement_ambiguity_count: 0,
          blocking_finding_count: 1,
          review_required_finding_count: 0,
          suppressed_without_evidence_count: 0,
        },
        readiness: {
          status: 'NOT_READY',
          ready: false,
          finding_count: 1,
          blocking_finding_count: 1,
          review_required_finding_count: 0,
          blocking_finding_ids: ['requirement:new-type'],
          review_required_finding_ids: [],
          counts_by_type: {
            requirement_conflict: 1,
            requirement_missing: 0,
            requirement_ambiguity: 0,
          },
          quality_claim: 'DETERMINISTIC_FINDING_GATE_NOT_COMPLETENESS_OR_RECALL',
        },
        findings: [
          {
            finding_id: 'requirement:new-type',
            finding_type: 'requirement_future_category',
            title: 'future',
            blocking: true,
            evidence: [{ source_id: 'prd', source_locator: 'PRD.md#1', quote: 'text' }],
          },
        ],
      },
    })).toThrow(/finding_type/);
  });
});
