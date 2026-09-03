import { asArray, asRecord, asString } from '../lib/value-guards';
import { API_V1_BASE, fetchJSON, resolveProjectId } from './session';

export type TestObligationKind =
  | 'business_rule'
  | 'lifecycle_transition'
  | 'authorization'
  | 'side_effect'
  | 'requirement_risk';

export type TestCoverageStatus = 'NOT_MEASURED' | 'PARTIAL' | 'COVERED';

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

export type TestIntelligenceSummary = {
  sourceCount: number;
  obligationCount: number;
  eligibleSupportedSemanticUnitCount: number;
  uncoveredSupportedSemanticUnitCount: number;
  suppressedWithoutEvidenceCount: number;
  unsupportedFormalBehaviorCount: number;
  requirementFindingLinkedObligationCount: number;
  implementedObligationKinds: TestObligationKind[];
};

export type TestIntelligenceAnalysis = {
  schema: string;
  productId: 'test_intelligence';
  projectId: string;
  analysisStatus: TestCoverageStatus;
  summary: TestIntelligenceSummary;
  coverage: TestCoverage;
  obligations: TestObligation[];
};

const ANALYSIS_SCHEMA = 'qualibug.test-intelligence.analysis.v1';
const COVERAGE_SCHEMA = 'qualibug.test-intelligence.coverage.v1';
const OBLIGATION_SCHEMA = 'qualibug.test-obligation.v1';
const QUALITY_CLAIM = 'DETERMINISTIC_SUPPORTED_SEMANTIC_OBLIGATION_COVERAGE_NOT_TOTAL_TEST_COMPLETENESS';

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

function parseEvidence(value: unknown, obligationId: string): TestEvidence[] {
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
  if (!valid.length) throw contractError(`obligations[${obligationId}].evidence`);
  return valid;
}

function parseStructuredArray(value: unknown): unknown[] {
  return asArray(value).flatMap((item) => {
    if (typeof item === 'string' && item.trim()) return [item.trim()];
    if (item && typeof item === 'object' && !Array.isArray(item)) return [asRecord(item)];
    return [];
  });
}

function parseExpectedOutcomes(value: unknown, obligationId: string): Record<string, unknown>[] {
  const rows = asArray(value)
    .filter((item) => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
    .map(asRecord)
    .filter((row) => Object.keys(row).length > 0);
  if (!rows.length) throw contractError(`obligations[${obligationId}].expected_outcomes`);
  return rows;
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
    evidence: parseEvidence(row.evidence, obligationId),
    derivedFrom: parseDerivedFrom(row.derived_from),
    requirementFindingIds: asStringArray(row.requirement_finding_ids),
    designStatus: requireExact(row.design_status, 'OBLIGATION_ONLY', `obligations[${obligationId}].design_status`) as 'OBLIGATION_ONLY',
    verificationStatus: requireExact(row.verification_status, 'NOT_MEASURED', `obligations[${obligationId}].verification_status`) as 'NOT_MEASURED',
    runtimeLinkage: requireExact(row.runtime_linkage, 'NOT_EVALUATED', `obligations[${obligationId}].runtime_linkage`) as 'NOT_EVALUATED',
    riskLevel: requireExact(row.risk_level, 'NOT_ASSESSED', `obligations[${obligationId}].risk_level`) as 'NOT_ASSESSED',
  };
}

export function parseTestIntelligenceAnalysis(value: unknown): TestIntelligenceAnalysis {
  const envelope = asRecord(value);
  const data = asRecord(envelope.data || value);
  const summary = asRecord(data.summary);
  const coverage = asRecord(data.coverage);
  const rawCounts = asRecord(coverage.counts_by_obligation_kind);

  requireExact(data.schema, ANALYSIS_SCHEMA, 'schema');
  requireExact(data.product_id, 'test_intelligence', 'product_id');
  requireExact(coverage.schema, COVERAGE_SCHEMA, 'coverage.schema');

  const status = parseCoverageStatus(coverage.status, 'coverage.status');
  const analysisStatus = parseCoverageStatus(data.analysis_status, 'analysis_status');
  if (analysisStatus !== status) throw contractError('analysis_status/coverage.status');

  const qualityClaim = requireString(coverage.quality_claim, 'coverage.quality_claim');
  if (qualityClaim !== QUALITY_CLAIM) throw contractError('coverage.quality_claim');
  requireExact(coverage.execution_coverage_status, 'NOT_MEASURED', 'coverage.execution_coverage_status');

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
      requirementFindingLinkedObligationCount: requireNumber(summary.requirement_finding_linked_obligation_count, 'summary.requirement_finding_linked_obligation_count'),
      implementedObligationKinds: asArray(summary.implemented_obligation_kinds).map((kind, index) => parseKind(kind, `summary.implemented_obligation_kinds[${index}]`)),
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
  };
}

export async function getTestIntelligence(projectId: string): Promise<TestIntelligenceAnalysis | null> {
  const project = await resolveProjectId(projectId);
  if (!project) return null;
  const payload = await fetchJSON<unknown>(
    `${API_V1_BASE}/projects/${encodeURIComponent(project)}/test-intelligence`,
  );
  return parseTestIntelligenceAnalysis(payload);
}
