import { describe, expect, it } from 'vitest';
import { parseTestIntelligenceAnalysis } from '../test-intelligence';

function validPayload() {
  return {
    ok: true,
    data: {
      schema: 'qualibug.test-intelligence.analysis.v1',
      product_id: 'test_intelligence',
      project_id: 'p1',
      analysis_status: 'COVERED',
      summary: {
        source_count: 2,
        obligation_count: 1,
        eligible_supported_semantic_unit_count: 1,
        uncovered_supported_semantic_unit_count: 0,
        suppressed_without_evidence_count: 0,
        unsupported_formal_behavior_count: 0,
        requirement_finding_linked_obligation_count: 0,
        implemented_obligation_kinds: ['business_rule', 'lifecycle_transition', 'authorization', 'side_effect'],
      },
      coverage: {
        schema: 'qualibug.test-intelligence.coverage.v1',
        status: 'COVERED',
        eligible_supported_semantic_unit_count: 1,
        obligated_supported_semantic_unit_count: 1,
        uncovered_supported_semantic_unit_count: 0,
        uncovered_supported_semantic_unit_ids: [],
        counts_by_obligation_kind: {
          business_rule: 0,
          lifecycle_transition: 0,
          authorization: 1,
          side_effect: 0,
          requirement_risk: 0,
        },
        execution_coverage_status: 'NOT_MEASURED',
        quality_claim: 'DETERMINISTIC_SUPPORTED_SEMANTIC_OBLIGATION_COVERAGE_NOT_TOTAL_TEST_COMPLETENESS',
      },
      obligations: [
        {
          schema: 'qualibug.test-obligation.v1',
          obligation_id: 'test-obligation:auth',
          obligation_kind: 'authorization',
          source_unit_id: 'behavior:b1:authorization',
          source_behavior_id: 'b1',
          source_transition_id: '',
          title: '客服对订单执行“取消”的授权边界',
          objective: '验证来源明确声明的角色、操作与业务对象授权决策。',
          actor_refs: ['客服'],
          object_refs: ['订单'],
          operation_ref: '取消',
          preconditions: [],
          expected_outcomes: [{ kind: 'authorization_decision', decision: 'DENY' }],
          business_constraints: [],
          source_refs: ['fact:auth'],
          source_ids: ['prd'],
          evidence: [{ source_id: 'prd', source_locator: 'PRD.md#line=20', quote: '客服不得取消已支付订单', fact_id: 'fact:auth' }],
          derived_from: [{ kind: 'business_behavior', id: 'b1' }],
          requirement_finding_ids: [],
          design_status: 'OBLIGATION_ONLY',
          verification_status: 'NOT_MEASURED',
          runtime_linkage: 'NOT_EVALUATED',
          risk_level: 'NOT_ASSESSED',
        },
      ],
    },
  };
}

describe('Test Intelligence frontend projection', () => {
  it('preserves obligation truth, source evidence and non-execution state', () => {
    const analysis = parseTestIntelligenceAnalysis(validPayload());
    expect(analysis.coverage.status).toBe('COVERED');
    expect(analysis.coverage.executionCoverageStatus).toBe('NOT_MEASURED');
    expect(analysis.obligations).toHaveLength(1);
    expect(analysis.obligations[0]).toMatchObject({
      obligationKind: 'authorization',
      designStatus: 'OBLIGATION_ONLY',
      verificationStatus: 'NOT_MEASURED',
      runtimeLinkage: 'NOT_EVALUATED',
    });
    expect(analysis.obligations[0].evidence[0]).toMatchObject({
      sourceId: 'prd',
      sourceLocator: 'PRD.md#line=20',
      quote: '客服不得取消已支付订单',
    });
  });

  it('fails closed on unknown coverage status', () => {
    const payload = validPayload();
    payload.data.analysis_status = 'HEALTHY';
    payload.data.coverage.status = 'HEALTHY';
    expect(() => parseTestIntelligenceAnalysis(payload)).toThrow(/coverage.status/);
  });

  it('fails closed when an obligation pretends it was verified or executed', () => {
    const payload = validPayload();
    payload.data.obligations[0].verification_status = 'VERIFIED';
    expect(() => parseTestIntelligenceAnalysis(payload)).toThrow(/verification_status/);
  });

  it('fails closed on unknown obligation kinds', () => {
    const payload = validPayload();
    payload.data.obligations[0].obligation_kind = 'future_magic_test';
    expect(() => parseTestIntelligenceAnalysis(payload)).toThrow(/obligation_kind/);
  });

  it('allows an honest empty NOT_MEASURED universe instead of inventing 100 percent coverage', () => {
    const payload = validPayload();
    payload.data.analysis_status = 'NOT_MEASURED';
    payload.data.summary.obligation_count = 0;
    payload.data.summary.eligible_supported_semantic_unit_count = 0;
    payload.data.coverage.status = 'NOT_MEASURED';
    payload.data.coverage.eligible_supported_semantic_unit_count = 0;
    payload.data.coverage.obligated_supported_semantic_unit_count = 0;
    payload.data.coverage.counts_by_obligation_kind.authorization = 0;
    payload.data.obligations = [];
    const analysis = parseTestIntelligenceAnalysis(payload);
    expect(analysis.coverage.status).toBe('NOT_MEASURED');
    expect(analysis.coverage.eligibleSupportedSemanticUnitCount).toBe(0);
  });
});
