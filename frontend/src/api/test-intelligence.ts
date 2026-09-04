import { asArray, asRecord, asString } from '../lib/value-guards';
import { API_V1_BASE, fetchJSON } from './session';

export type TestObligationKind =
  | 'business_rule'
  | 'lifecycle_transition'
  | 'authorization'
  | 'side_effect'
  | 'requirement_risk';

export type TestCoverageStatus = 'NOT_MEASURED' | 'PARTIAL' | 'COVERED';
export type TestDesignProjectionStatus = 'NOT_MEASURED' | 'PARTIAL' | 'DESIGNED';

export type TestEvidence = {
  sourceId: string;
  sourceLocator: string;
  assetRef: string;
  documentBlockId: string;
  documentNodeId: string;
  quote: string;
  quoteHash: string;
  factId: string;
  derivation: string;
};

export type TestDerivedFrom = {
  kind: string;
  id: string;
};

export type TestObligation = {
  obligationId: string;
  obligationKind: TestObligationKind;
  sourceUnitId: string;
  sourceBehaviorId: string;
  sourceTransitionId: string;
  title: string;
  objective: string;
  actorRefs: string[];
  objectRefs: string[];
  operationRef: string;
  preconditions: unknown[];
  expectedOutcomes: Record<string, unknown>[];
  businessConstraints: string[];
  sourceRefs: string[];
  sourceIds: string[];
  evidence: TestEvidence[];
  derivedFrom: TestDerivedFrom[];
  requirementFindingIds: string[];
  designStatus: 'OBLIGATION_ONLY';
  verificationStatus: 'NOT_MEASURED';
  runtimeLinkage: 'NOT_EVALUATED';
  riskLevel: 'NOT_ASSESSED';
};

export type TestDesignObservation = {
  observationKind: string;
  target: string;
  bindingStatus: 'NOT_GROUNDED';
  expected: Record<string, unknown>;
  objectRefs: string[];
  objectRef: string;
  field: string;
};

export type TestDesign = {
  designId: string;
  sourceObligationId: string;
  obligationKind: TestObligationKind;
  title: string;
  objective: string;
  setup: {
    actorRefs: string[];
    objectRefs: string[];
    preconditions: unknown[];
    testDataRequirements: unknown[];
    testDataMaterializationStatus: 'NOT_MATERIALIZED';
    environmentStatus: 'NOT_SELECTED';
  };
  action: {
    operationRef: string;
    actorRefs: string[];
    objectRefs: string[];
    executionSurface: 'NOT_SELECTED';
    bindingStatus: 'NOT_GROUNDED';
  };
  observations: TestDesignObservation[];
  oracle: {
    assertions: Record<string, unknown>[];
    semanticStatus: 'SOURCE_DERIVED';
    bindingStatus: 'NOT_GROUNDED';
  };
  businessConstraints: string[];
  sourceRefs: string[];
  sourceIds: string[];
  evidence: TestEvidence[];
  derivedFrom: TestDerivedFrom[];
  requirementFindingIds: string[];
  designStatus: 'STRUCTURED_DESIGN_ONLY';
  observerBindingStatus: 'NOT_GROUNDED';
  oracleBindingStatus: 'NOT_GROUNDED';
  runtimeHandoffStatus: 'NOT_REQUESTED';
  executionStatus: 'NOT_EXECUTED';
  safetyReviewStatus: 'NOT_ASSESSED';
};

export type TestCoverage = {
  status: TestCoverageStatus;
  eligibleSupportedSemanticUnitCount: number;
  obligatedSupportedSemanticUnitCount: number;
  uncoveredSupportedSemanticUnitCount: number;
  uncoveredSupportedSemanticUnitIds: string[];
  countsByObligationKind: Record<TestObligationKind, number>;
  executionCoverageStatus: 'NOT_MEASURED';
  qualityClaim: string;
};

export type TestDesignProjection = {
  status: TestDesignProjectionStatus;
  eligibleObligationCount: number;
  designedObligationCount: number;
  undesignedObligationCount: number;
  undesignedObligationIds: string[];
  runtimeGroundingStatus: 'NOT_GROUNDED';
  runtimeExecutionStatus: 'NOT_EXECUTED';
  qualityClaim: string;
};

export type TestIntelligenceSummary = {
  sourceCount: number;
  obligationCount: number;
  eligibleSupportedSemanticUnitCount: number;
  uncoveredSupportedSemanticUnitCount: number;
  suppressedWithoutEvidenceCount: number;
  unsupportedFormalBehaviorCount: number;
  requirementFindingLinkedObligationCount: number;
  requirementFindingLinkedDesignCount: number;
  implementedObligationKinds: TestObligationKind[];
  testDesignCount: number;
  undesignedObligationCount: number;
  testDesignStatus: TestDesignProjectionStatus;
};

export type TestIntelligenceAnalysis = {
  schema: string;
  productId: 'test_intelligence';
  projectId: string;
  analysisStatus: TestCoverageStatus;
  summary: TestIntelligenceSummary;
  coverage: TestCoverage;
  obligations: TestObligation[];
  testDesignProjection: TestDesignProjection;
  testDesigns: TestDesign[];
};

const ANALYSIS_SCHEMA = 'qualibug.test-intelligence.analysis.v1';
const COVERAGE_SCHEMA = 'qualibug.test-intelligence.coverage.v1';
const OBLIGATION_SCHEMA = 'qualibug.test-obligation.v1';
const TEST_DESIGN_SCHEMA = 'qualibug.test-design.v1';
const TEST_DESIGN_PROJECTION_SCHEMA = 'qualibug.test-design-projection.v1';
const QUALITY_CLAIM = 'DETERMINISTIC_SUPPORTED_SEMANTIC_OBLIGATION_COVERAGE_NOT_TOTAL_TEST_COMPLETENESS';
const TEST_DESIGN_QUALITY_CLAIM = 'DETERMINISTIC_OBLIGATION_DERIVED_TEST_DESIGN_NOT_RUNTIME_GROUNDING_OR_EXECUTION';

function contractError(field: string): Error {
  return new Error(`测试智能响应缺少或包含无效字段：${field}`);
}

function requireNumber(value: unknown, field: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw contractError(field);
  return value;
}

function requireString(value: unknown, field: string): string {
  const text = asString(value);
  if (!text) throw contractError(field);
  return text;
}

function requireExact(value: unknown, expected: string, field: string): string {
  const text = requireString(value, field);
  if (text !== expected) throw contractError(field);
  return text;
}

function asStringArray(value: unknown): string[] {
  return asArray(value).map(asString).filter(Boolean);
}

function sameStrings(left: string[], right: string[]): boolean {
  const a = [...new Set(left)].sort();
  const b = [...new Set(right)].sort();
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

function parseKind(value: unknown, field = 'obligations[].obligation_kind'): TestObligationKind {
  const kind = asString(value);
  if (
    kind === 'business_rule'
    || kind === 'lifecycle_transition'
    || kind === 'authorization'
    || kind === 'side_effect'
    || kind === 'requirement_risk'
  ) return kind;
  throw contractError(field);
}

function parseCoverageStatus(value: unknown, field: string): TestCoverageStatus {
  const status = asString(value).toUpperCase();
  if (status === 'NOT_MEASURED' || status === 'PARTIAL' || status === 'COVERED') return status;
  throw contractError(field);
}

function parseDesignProjectionStatus(value: unknown, field: string): TestDesignProjectionStatus {
  const status = asString(value).toUpperCase();
  if (status === 'NOT_MEASURED' || status === 'PARTIAL' || status === 'DESIGNED') return status;
  throw contractError(field);
}

function parseEvidence(value: unknown, owner: string): TestEvidence[] {
  const evidence = asArray(value).map(asRecord).map((row) => ({
    sourceId: asString(row.source_id),
    sourceLocator: asString(row.source_locator),
    assetRef: asString(row.asset_ref),
    documentBlockId: asString(row.document_block_id),
    documentNodeId: asString(row.document_node_id),
    quote: asString(row.quote),
    quoteHash: asString(row.quote_hash),
    factId: asString(row.fact_id),
    derivation: asString(row.derivation),
  }));
  const valid = evidence.filter((row) => {
    const hasAnchor = Boolean(row.sourceLocator || row.assetRef || row.documentBlockId || row.documentNodeId);
    const hasContent = Boolean(row.quote || row.quoteHash);
    return Boolean(row.sourceId && hasAnchor && hasContent);
  });
  if (!valid.length) throw contractError(`${owner}.evidence`);
  return valid;
}

function parseStructuredArray(value: unknown): unknown[] {
  const result: unknown[] = [];
  for (const item of asArray(value)) {
    if (typeof item === 'string' && item.trim()) result.push(item.trim());
    else if (item && typeof item === 'object' && !Array.isArray(item)) result.push(asRecord(item));
  }
  return result;
}

function parseRecordArray(value: unknown, field: string): Record<string, unknown>[] {
  const rows = asArray(value)
    .filter((item) => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    .map(asRecord)
    .filter((row) => Object.keys(row).length > 0);
  if (!rows.length) throw contractError(field);
  return rows;
}

function parseExpectedOutcomes(value: unknown, obligationId: string): Record<string, unknown>[] {
  return parseRecordArray(value, `obligations[${obligationId}].expected_outcomes`);
}

function parseDerivedFrom(value: unknown): TestDerivedFrom[] {
  return asArray(value).map(asRecord).flatMap((row) => {
    const kind = asString(row.kind);
    const id = asString(row.id);
    return kind && id ? [{ kind, id }] : [];
  });
}

function parseObligation(value: unknown): TestObligation {
  const row = asRecord(value);
  const obligationId = requireString(row.obligation_id, 'obligations[].obligation_id');
  requireExact(row.schema, OBLIGATION_SCHEMA, `obligations[${obligationId}].schema`);
  return {
    obligationId,
    obligationKind: parseKind(row.obligation_kind),
    sourceUnitId: requireString(row.source_unit_id, `obligations[${obligationId}].source_unit_id`),
    sourceBehaviorId: asString(row.source_behavior_id),
    sourceTransitionId: asString(row.source_transition_id),
    title: requireString(row.title, `obligations[${obligationId}].title`),
    objective: requireString(row.objective, `obligations[${obligationId}].objective`),
    actorRefs: asStringArray(row.actor_refs),
    objectRefs: asStringArray(row.object_refs),
    operationRef: asString(row.operation_ref),
    preconditions: parseStructuredArray(row.preconditions),
    expectedOutcomes: parseExpectedOutcomes(row.expected_outcomes, obligationId),
    businessConstraints: asStringArray(row.business_constraints),
    sourceRefs: asStringArray(row.source_refs),
    sourceIds: asStringArray(row.source_ids),
    evidence: parseEvidence(row.evidence, `obligations[${obligationId}]`),
    derivedFrom: parseDerivedFrom(row.derived_from),
    requirementFindingIds: asStringArray(row.requirement_finding_ids),
    designStatus: requireExact(row.design_status, 'OBLIGATION_ONLY', `obligations[${obligationId}].design_status`) as 'OBLIGATION_ONLY',
    verificationStatus: requireExact(row.verification_status, 'NOT_MEASURED', `obligations[${obligationId}].verification_status`) as 'NOT_MEASURED',
    runtimeLinkage: requireExact(row.runtime_linkage, 'NOT_EVALUATED', `obligations[${obligationId}].runtime_linkage`) as 'NOT_EVALUATED',
    riskLevel: requireExact(row.risk_level, 'NOT_ASSESSED', `obligations[${obligationId}].risk_level`) as 'NOT_ASSESSED',
  };
}

function parseObservation(value: unknown, designId: string, index: number): TestDesignObservation {
  const row = asRecord(value);
  const expected = asRecord(row.expected);
  if (!Object.keys(expected).length) throw contractError(`test_designs[${designId}].observations[${index}].expected`);
  return {
    observationKind: requireString(row.observation_kind, `test_designs[${designId}].observations[${index}].observation_kind`),
    target: requireString(row.target, `test_designs[${designId}].observations[${index}].target`),
    bindingStatus: requireExact(row.binding_status, 'NOT_GROUNDED', `test_designs[${designId}].observations[${index}].binding_status`) as 'NOT_GROUNDED',
    expected,
    objectRefs: asStringArray(row.object_refs),
    objectRef: asString(row.object_ref),
    field: asString(row.field),
  };
}

function parseTestDesign(value: unknown): TestDesign {
  const row = asRecord(value);
  const designId = requireString(row.design_id, 'test_designs[].design_id');
  requireExact(row.schema, TEST_DESIGN_SCHEMA, `test_designs[${designId}].schema`);
  const setup = asRecord(row.setup);
  const action = asRecord(row.action);
  const oracle = asRecord(row.oracle);
  const observations = asArray(row.observations).map((item, index) => parseObservation(item, designId, index));
  if (!observations.length) throw contractError(`test_designs[${designId}].observations`);
  const assertions = parseRecordArray(oracle.assertions, `test_designs[${designId}].oracle.assertions`);
  return {
    designId,
    sourceObligationId: requireString(row.source_obligation_id, `test_designs[${designId}].source_obligation_id`),
    obligationKind: parseKind(row.obligation_kind, `test_designs[${designId}].obligation_kind`),
    title: requireString(row.title, `test_designs[${designId}].title`),
    objective: requireString(row.objective, `test_designs[${designId}].objective`),
    setup: {
      actorRefs: asStringArray(setup.actor_refs),
      objectRefs: asStringArray(setup.object_refs),
      preconditions: parseStructuredArray(setup.preconditions),
      testDataRequirements: parseStructuredArray(setup.test_data_requirements),
      testDataMaterializationStatus: requireExact(setup.test_data_materialization_status, 'NOT_MATERIALIZED', `test_designs[${designId}].setup.test_data_materialization_status`) as 'NOT_MATERIALIZED',
      environmentStatus: requireExact(setup.environment_status, 'NOT_SELECTED', `test_designs[${designId}].setup.environment_status`) as 'NOT_SELECTED',
    },
    action: {
      operationRef: asString(action.operation_ref),
      actorRefs: asStringArray(action.actor_refs),
      objectRefs: asStringArray(action.object_refs),
      executionSurface: requireExact(action.execution_surface, 'NOT_SELECTED', `test_designs[${designId}].action.execution_surface`) as 'NOT_SELECTED',
      bindingStatus: requireExact(action.binding_status, 'NOT_GROUNDED', `test_designs[${designId}].action.binding_status`) as 'NOT_GROUNDED',
    },
    observations,
    oracle: {
      assertions,
      semanticStatus: requireExact(oracle.semantic_status, 'SOURCE_DERIVED', `test_designs[${designId}].oracle.semantic_status`) as 'SOURCE_DERIVED',
      bindingStatus: requireExact(oracle.binding_status, 'NOT_GROUNDED', `test_designs[${designId}].oracle.binding_status`) as 'NOT_GROUNDED',
    },
    businessConstraints: asStringArray(row.business_constraints),
    sourceRefs: asStringArray(row.source_refs),
    sourceIds: asStringArray(row.source_ids),
    evidence: parseEvidence(row.evidence, `test_designs[${designId}]`),
    derivedFrom: parseDerivedFrom(row.derived_from),
    requirementFindingIds: asStringArray(row.requirement_finding_ids),
    designStatus: requireExact(row.design_status, 'STRUCTURED_DESIGN_ONLY', `test_designs[${designId}].design_status`) as 'STRUCTURED_DESIGN_ONLY',
    observerBindingStatus: requireExact(row.observer_binding_status, 'NOT_GROUNDED', `test_designs[${designId}].observer_binding_status`) as 'NOT_GROUNDED',
    oracleBindingStatus: requireExact(row.oracle_binding_status, 'NOT_GROUNDED', `test_designs[${designId}].oracle_binding_status`) as 'NOT_GROUNDED',
    runtimeHandoffStatus: requireExact(row.runtime_handoff_status, 'NOT_REQUESTED', `test_designs[${designId}].runtime_handoff_status`) as 'NOT_REQUESTED',
    executionStatus: requireExact(row.execution_status, 'NOT_EXECUTED', `test_designs[${designId}].execution_status`) as 'NOT_EXECUTED',
    safetyReviewStatus: requireExact(row.safety_review_status, 'NOT_ASSESSED', `test_designs[${designId}].safety_review_status`) as 'NOT_ASSESSED',
  };
}

export function parseTestIntelligenceAnalysis(value: unknown): TestIntelligenceAnalysis {
  const envelope = asRecord(value);
  const data = asRecord(envelope.data || value);
  const summary = asRecord(data.summary);
  const coverage = asRecord(data.coverage);
  const designProjection = asRecord(data.test_design_projection);
  const rawCounts = asRecord(coverage.counts_by_obligation_kind);

  requireExact(data.schema, ANALYSIS_SCHEMA, 'schema');
  requireExact(data.product_id, 'test_intelligence', 'product_id');
  requireExact(coverage.schema, COVERAGE_SCHEMA, 'coverage.schema');
  requireExact(designProjection.schema, TEST_DESIGN_PROJECTION_SCHEMA, 'test_design_projection.schema');

  const status = parseCoverageStatus(coverage.status, 'coverage.status');
  const analysisStatus = parseCoverageStatus(data.analysis_status, 'analysis_status');
  if (analysisStatus !== status) throw contractError('analysis_status/coverage.status');

  const qualityClaim = requireString(coverage.quality_claim, 'coverage.quality_claim');
  if (qualityClaim !== QUALITY_CLAIM) throw contractError('coverage.quality_claim');
  requireExact(coverage.execution_coverage_status, 'NOT_MEASURED', 'coverage.execution_coverage_status');

  const designQualityClaim = requireString(designProjection.quality_claim, 'test_design_projection.quality_claim');
  if (designQualityClaim !== TEST_DESIGN_QUALITY_CLAIM) throw contractError('test_design_projection.quality_claim');
  const testDesignStatus = parseDesignProjectionStatus(designProjection.status, 'test_design_projection.status');
  requireExact(designProjection.runtime_grounding_status, 'NOT_GROUNDED', 'test_design_projection.runtime_grounding_status');
  requireExact(designProjection.runtime_execution_status, 'NOT_EXECUTED', 'test_design_projection.runtime_execution_status');

  const countsByObligationKind: Record<TestObligationKind, number> = {
    business_rule: requireNumber(rawCounts.business_rule, 'coverage.counts_by_obligation_kind.business_rule'),
    lifecycle_transition: requireNumber(rawCounts.lifecycle_transition, 'coverage.counts_by_obligation_kind.lifecycle_transition'),
    authorization: requireNumber(rawCounts.authorization, 'coverage.counts_by_obligation_kind.authorization'),
    side_effect: requireNumber(rawCounts.side_effect, 'coverage.counts_by_obligation_kind.side_effect'),
    requirement_risk: requireNumber(rawCounts.requirement_risk, 'coverage.counts_by_obligation_kind.requirement_risk'),
  };

  const obligations = asArray(data.obligations).map(parseObligation);
  const obligationCount = requireNumber(summary.obligation_count, 'summary.obligation_count');
  if (obligationCount !== obligations.length) throw contractError('summary.obligation_count/obligations');
  const countedObligations = Object.values(countsByObligationKind).reduce((total, count) => total + count, 0);
  if (countedObligations !== obligationCount) throw contractError('coverage.counts_by_obligation_kind');
  const requirementFindingLinkedObligationCount = requireNumber(
    summary.requirement_finding_linked_obligation_count,
    'summary.requirement_finding_linked_obligation_count',
  );
  const actualLinkedObligationCount = obligations.filter((item) => item.requirementFindingIds.length > 0).length;
  if (requirementFindingLinkedObligationCount !== actualLinkedObligationCount) {
    throw contractError('summary.requirement_finding_linked_obligation_count/obligations');
  }

  const designs = asArray(data.test_designs).map(parseTestDesign);
  const obligationById = new Map(obligations.map((item) => [item.obligationId, item]));
  for (const design of designs) {
    const source = obligationById.get(design.sourceObligationId);
    if (!source) throw contractError(`test_designs[${design.designId}].source_obligation_id`);
    if (design.obligationKind !== source.obligationKind) throw contractError(`test_designs[${design.designId}].obligation_kind/source_obligation`);
    if (!sameStrings(design.requirementFindingIds, source.requirementFindingIds)) {
      throw contractError(`test_designs[${design.designId}].requirement_finding_ids/source_obligation`);
    }
  }
  const testDesignCount = requireNumber(summary.test_design_count, 'summary.test_design_count');
  if (testDesignCount !== designs.length) throw contractError('summary.test_design_count/test_designs');
  const eligibleDesignObligations = requireNumber(designProjection.eligible_obligation_count, 'test_design_projection.eligible_obligation_count');
  const designedObligations = requireNumber(designProjection.designed_obligation_count, 'test_design_projection.designed_obligation_count');
  const undesignedObligations = requireNumber(designProjection.undesigned_obligation_count, 'test_design_projection.undesigned_obligation_count');
  const undesignedObligationIds = asStringArray(designProjection.undesigned_obligation_ids);
  if (eligibleDesignObligations !== obligationCount) throw contractError('test_design_projection.eligible_obligation_count/obligations');
  if (undesignedObligationIds.length !== undesignedObligations) throw contractError('test_design_projection.undesigned_obligation_ids');
  if (designedObligations + undesignedObligations !== eligibleDesignObligations) throw contractError('test_design_projection.obligation_counts');
  const uniquelyDesignedObligations = new Set(designs.map((item) => item.sourceObligationId)).size;
  if (uniquelyDesignedObligations !== designedObligations) throw contractError('test_design_projection.designed_obligation_count');
  if (testDesignCount !== designedObligations) throw contractError('summary.test_design_count/test_design_projection');
  const summaryUndesigned = requireNumber(summary.undesigned_obligation_count, 'summary.undesigned_obligation_count');
  if (summaryUndesigned !== undesignedObligations) throw contractError('summary.undesigned_obligation_count/test_design_projection');
  const summaryDesignStatus = parseDesignProjectionStatus(summary.test_design_status, 'summary.test_design_status');
  if (summaryDesignStatus !== testDesignStatus) throw contractError('summary.test_design_status/test_design_projection.status');
  const requirementFindingLinkedDesignCount = requireNumber(
    summary.requirement_finding_linked_design_count,
    'summary.requirement_finding_linked_design_count',
  );
  const actualLinkedDesignCount = designs.filter((item) => item.requirementFindingIds.length > 0).length;
  if (requirementFindingLinkedDesignCount !== actualLinkedDesignCount) {
    throw contractError('summary.requirement_finding_linked_design_count/test_designs');
  }

  const eligible = requireNumber(coverage.eligible_supported_semantic_unit_count, 'coverage.eligible_supported_semantic_unit_count');
  const obligated = requireNumber(coverage.obligated_supported_semantic_unit_count, 'coverage.obligated_supported_semantic_unit_count');
  const uncovered = requireNumber(coverage.uncovered_supported_semantic_unit_count, 'coverage.uncovered_supported_semantic_unit_count');
  const uncoveredIds = asStringArray(coverage.uncovered_supported_semantic_unit_ids);
  if (uncoveredIds.length !== uncovered) throw contractError('coverage.uncovered_supported_semantic_unit_ids');
  if (obligated + uncovered !== eligible) throw contractError('coverage.supported_semantic_unit_counts');
  const uniqueObligatedUnits = new Set(obligations.map((item) => item.sourceUnitId)).size;
  if (uniqueObligatedUnits !== obligated) throw contractError('coverage.obligated_supported_semantic_unit_count');

  const summaryEligible = requireNumber(summary.eligible_supported_semantic_unit_count, 'summary.eligible_supported_semantic_unit_count');
  const summaryUncovered = requireNumber(summary.uncovered_supported_semantic_unit_count, 'summary.uncovered_supported_semantic_unit_count');
  if (summaryEligible !== eligible || summaryUncovered !== uncovered) throw contractError('summary/coverage.semantic_unit_counts');

  return {
    schema: ANALYSIS_SCHEMA,
    productId: 'test_intelligence',
    projectId: requireString(data.project_id, 'project_id'),
    analysisStatus,
    summary: {
      sourceCount: requireNumber(summary.source_count, 'summary.source_count'),
      obligationCount,
      eligibleSupportedSemanticUnitCount: summaryEligible,
      uncoveredSupportedSemanticUnitCount: summaryUncovered,
      suppressedWithoutEvidenceCount: requireNumber(summary.suppressed_without_evidence_count, 'summary.suppressed_without_evidence_count'),
      unsupportedFormalBehaviorCount: requireNumber(summary.unsupported_formal_behavior_count, 'summary.unsupported_formal_behavior_count'),
      requirementFindingLinkedObligationCount,
      requirementFindingLinkedDesignCount,
      implementedObligationKinds: asArray(summary.implemented_obligation_kinds).map((kind, index) => parseKind(kind, `summary.implemented_obligation_kinds[${index}]`)),
      testDesignCount,
      undesignedObligationCount: summaryUndesigned,
      testDesignStatus: summaryDesignStatus,
    },
    coverage: {
      status,
      eligibleSupportedSemanticUnitCount: eligible,
      obligatedSupportedSemanticUnitCount: obligated,
      uncoveredSupportedSemanticUnitCount: uncovered,
      uncoveredSupportedSemanticUnitIds: uncoveredIds,
      countsByObligationKind,
      executionCoverageStatus: 'NOT_MEASURED',
      qualityClaim,
    },
    obligations,
    testDesignProjection: {
      status: testDesignStatus,
      eligibleObligationCount: eligibleDesignObligations,
      designedObligationCount: designedObligations,
      undesignedObligationCount: undesignedObligations,
      undesignedObligationIds,
      runtimeGroundingStatus: 'NOT_GROUNDED',
      runtimeExecutionStatus: 'NOT_EXECUTED',
      qualityClaim: designQualityClaim,
    },
    testDesigns: designs,
  };
}

export async function getTestIntelligence(projectId: string): Promise<TestIntelligenceAnalysis | null> {
  const project = projectId.trim();
  if (!project) return null;
  const payload = await fetchJSON<unknown>(
    `${API_V1_BASE}/projects/${encodeURIComponent(project)}/test-intelligence`,
  );
  return parseTestIntelligenceAnalysis(payload);
}
