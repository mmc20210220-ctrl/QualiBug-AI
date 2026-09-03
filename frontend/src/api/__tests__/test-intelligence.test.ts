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
        requirement_finding_linked_design_count: 0,
        implemented_obligation_kinds: ['business_rule', 'lifecycle_transition', 'authorization', 'side_effect'],
        test_design_count: 1,
        undesigned_obligation_count: 0,
        test_design_status: 'DESIGNED',
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
      test_design_projection: {
        schema: 'qualibug.test-design-projection.v1',
        status: 'DESIGNED',
        quality_claim: 'DETERMINISTIC_OBLIGATION_DERIVED_TEST_DESIGN_NOT_RUNTIME_GROUNDING_OR_EXECUTION',
        eligible_obligation_count: 1,
        designed_obligation_count: 1,
        undesigned_obligation_count: 0,
        undesigned_obligation_ids: [],
        runtime_grounding_status: 'NOT_GROUNDED',
        runtime_execution_status: 'NOT_EXECUTED',
      },
      test_designs: [
        {
          schema: 'qualibug.test-design.v1',
          design_id: 'test-design:auth',
          source_obligation_id: 'test-obligation:auth',
          obligation_kind: 'authorization',
          title: '测试设计：客服对订单执行“取消”的授权边界',
          objective: '验证来源明确声明的角色、操作与业务对象授权决策。',
          setup: {
            actor_refs: ['客服'],
            object_refs: ['订单'],
            preconditions: [],
            test_data_requirements: [],
            test_data_materialization_status: 'NOT_MATERIALIZED',
            environment_status: 'NOT_SELECTED',
          },
          action: {
            operation_ref: '取消',
            actor_refs: ['客服'],
            object_refs: ['订单'],
            execution_surface: 'NOT_SELECTED',
            binding_status: 'NOT_GROUNDED',
          },
          observations: [
            {
              observation_kind: 'authorization_decision',
              target: 'operation_authorization_result',
              binding_status: 'NOT_GROUNDED',
              expected: { kind: 'authorization_decision', decision: 'DENY' },
            },
          ],
          oracle: {
            assertions: [{ kind: 'authorization_decision', decision: 'DENY' }],
            semantic_status: 'SOURCE_DERIVED',
            binding_status: 'NOT_GROUNDED',
          },
          business_constraints: [],
          source_refs: ['fact:auth'],
          source_ids: ['prd'],
          evidence: [{ source_id: 'prd', source_locator: 'PRD.md#line=20', quote: '客服不得取消已支付订单', fact_id: 'fact:auth' }],
          derived_from: [{ kind: 'test_obligation', id: 'test-obligation:auth' }],
          requirement_finding_ids: [],
          design_status: 'STRUCTURED_DESIGN_ONLY',
          observer_binding_status: 'NOT_GROUNDED',
          oracle_binding_status: 'NOT_GROUNDED',
          runtime_handoff_status: 'NOT_REQUESTED',
          execution_status: 'NOT_EXECUTED',
          safety_review_status: 'NOT_ASSESSED',
        },
      ],
    },
  };
}

describe('Test Intelligence frontend projection', () => {
  it('preserves obligation truth, structured design and non-execution state', () => {
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
    expect(analysis.testDesignProjection).toMatchObject({
      status: 'DESIGNED',
      runtimeGroundingStatus: 'NOT_GROUNDED',
      runtimeExecutionStatus: 'NOT_EXECUTED',
    });
    expect(analysis.testDesigns[0]).toMatchObject({
      sourceObligationId: 'test-obligation:auth',
      designStatus: 'STRUCTURED_DESIGN_ONLY',
      runtimeHandoffStatus: 'NOT_REQUESTED',
      executionStatus: 'NOT_EXECUTED',
    });
    expect(analysis.testDesigns[0].action).toMatchObject({
      executionSurface: 'NOT_SELECTED',
      bindingStatus: 'NOT_GROUNDED',
    });
  });

  it('preserves backend-proven Requirement Finding ids on obligation and inherited design', () => {
    const payload = validPayload();
    payload.data.summary.requirement_finding_linked_obligation_count = 1;
    payload.data.summary.requirement_finding_linked_design_count = 1;
    payload.data.obligations[0].requirement_finding_ids = ['requirement:conflict:cancel-rule'];
    payload.data.test_designs[0].requirement_finding_ids = ['requirement:conflict:cancel-rule'];
    const analysis = parseTestIntelligenceAnalysis(payload);
    expect(analysis.summary.requirementFindingLinkedObligationCount).toBe(1);
    expect(analysis.summary.requirementFindingLinkedDesignCount).toBe(1);
    expect(analysis.testDesigns[0].requirementFindingIds).toEqual(['requirement:conflict:cancel-rule']);
  });

  it('fails closed when the linkage summary disagrees with actual linked obligations', () => {
    const payload = validPayload();
    payload.data.summary.requirement_finding_linked_obligation_count = 1;
    expect(() => parseTestIntelligenceAnalysis(payload)).toThrow(/requirement_finding_linked_obligation_count/);
  });

  it('fails closed when a Test Design points to a nonexistent obligation', () => {
    const payload = validPayload();
    payload.data.test_designs[0].source_obligation_id = 'test-obligation:missing';
    expect(() => parseTestIntelligenceAnalysis(payload)).toThrow(/source_obligation_id/);
  });

  it('fails closed when a Test Design claims runtime grounding or execution', () => {
    const payload = validPayload();
    payload.data.test_designs[0].action.binding_status = 'GROUNDED';
    expect(() => parseTestIntelligenceAnalysis(payload)).toThrow(/action.binding_status/);

    const executed = validPayload();
    executed.data.test_designs[0].execution_status = 'EXECUTED';
    expect(() => parseTestIntelligenceAnalysis(executed)).toThrow(/execution_status/);
  });

  it('fails closed when design linkage differs from its source obligation linkage', () => {
    const payload = validPayload();
    payload.data.summary.requirement_finding_linked_obligation_count = 1;
    payload.data.obligations[0].requirement_finding_ids = ['requirement:conflict:cancel-rule'];
    expect(() => parseTestIntelligenceAnalysis(payload)).toThrow(/requirement_finding_ids\/source_obligation/);
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

  it('allows honest empty NOT_MEASURED universes instead of inventing coverage or design', () => {
    const payload = validPayload();
    payload.data.analysis_status = 'NOT_MEASURED';
    payload.data.summary.obligation_count = 0;
    payload.data.summary.eligible_supported_semantic_unit_count = 0;
    payload.data.summary.test_design_count = 0;
    payload.data.summary.test_design_status = 'NOT_MEASURED';
    payload.data.coverage.status = 'NOT_MEASURED';
    payload.data.coverage.eligible_supported_semantic_unit_count = 0;
    payload.data.coverage.obligated_supported_semantic_unit_count = 0;
    payload.data.coverage.counts_by_obligation_kind.authorization = 0;
    payload.data.obligations = [];
    payload.data.test_design_projection.status = 'NOT_MEASURED';
    payload.data.test_design_projection.eligible_obligation_count = 0;
    payload.data.test_design_projection.designed_obligation_count = 0;
    payload.data.test_designs = [];
    const analysis = parseTestIntelligenceAnalysis(payload);
    expect(analysis.coverage.status).toBe('NOT_MEASURED');
    expect(analysis.testDesignProjection.status).toBe('NOT_MEASURED');
    expect(analysis.testDesigns).toEqual([]);
  });
});