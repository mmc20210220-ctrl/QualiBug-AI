import { asArray, asRecord, asString } from '../lib/value-guards';
import { API_V1_BASE, fetchJSON } from './session';

export type RequirementFindingType =
  | 'requirement_conflict'
  | 'requirement_missing'
  | 'requirement_ambiguity';

export type RequirementReadinessStatus = 'NOT_READY' | 'REVIEW_REQUIRED' | 'READY';

export type RequirementEvidence = {
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

export type RequirementFinding = {
  findingId: string;
  findingType: RequirementFindingType;
  title: string;
  description: string;
  status: string;
  severity: string;
  blocking: boolean;
  sourceIds: string[];
  evidence: RequirementEvidence[];
  operatorAction: string;
  relatedObjectRefs: string[];
  relatedOperationRefs: string[];
  reviewStatus: string;
  reasonCode: string;
};

export type RequirementReadiness = {
  status: RequirementReadinessStatus;
  ready: boolean;
  findingCount: number;
  blockingFindingCount: number;
  reviewRequiredFindingCount: number;
  blockingFindingIds: string[];
  reviewRequiredFindingIds: string[];
  countsByType: Record<RequirementFindingType, number>;
  qualityClaim: string;
};

export type RequirementAnalysisSummary = {
  sourceCount: number;
  conflictCount: number;
  missingCount: number;
  ambiguityCount: number;
  blockingFindingCount: number;
  reviewRequiredFindingCount: number;
  suppressedWithoutEvidenceCount: number;
};

export type RequirementIntelligenceAnalysis = {
  schema: string;
  productId: string;
  projectId: string;
  analysisStatus: RequirementReadinessStatus;
  summary: RequirementAnalysisSummary;
  readiness: RequirementReadiness;
  findings: RequirementFinding[];
};

const QUALITY_CLAIM = 'DETERMINISTIC_FINDING_GATE_NOT_COMPLETENESS_OR_RECALL';

function contractError(field: string): Error {
  return new Error(`需求审查响应缺少或包含无效字段：${field}`);
}

function requireBoolean(value: unknown, field: string): boolean {
  if (typeof value !== 'boolean') throw contractError(field);
  return value;
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

function asStringArray(value: unknown): string[] {
  return asArray(value).map(asString).filter(Boolean);
}

function parseFindingType(value: unknown): RequirementFindingType {
  const type = asString(value);
  if (
    type === 'requirement_conflict'
    || type === 'requirement_missing'
    || type === 'requirement_ambiguity'
  ) return type;
  throw contractError('findings[].finding_type');
}

function parseReadinessStatus(value: unknown, field: string): RequirementReadinessStatus {
  const status = asString(value).toUpperCase();
  if (status === 'READY' || status === 'REVIEW_REQUIRED' || status === 'NOT_READY') return status;
  throw contractError(field);
}

function parseEvidence(value: unknown, findingId: string): RequirementEvidence[] {
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
  })).filter((row) => Boolean(row.sourceId || row.sourceLocator || row.assetRef || row.quote || row.factId));
  if (!evidence.length) throw contractError(`findings[${findingId}].evidence`);
  return evidence;
}

function parseFinding(value: unknown): RequirementFinding {
  const row = asRecord(value);
  const findingId = requireString(row.finding_id, 'findings[].finding_id');
  const findingType = parseFindingType(row.finding_type);
  return {
    findingId,
    findingType,
    title: requireString(row.title, `findings[${findingId}].title`),
    description: asString(row.description),
    status: asString(row.status),
    severity: asString(row.severity),
    blocking: requireBoolean(row.blocking, `findings[${findingId}].blocking`),
    sourceIds: asStringArray(row.source_ids),
    evidence: parseEvidence(row.evidence, findingId),
    operatorAction: asString(row.operator_action),
    relatedObjectRefs: asStringArray(row.related_object_refs),
    relatedOperationRefs: asStringArray(row.related_operation_refs),
    reviewStatus: asString(row.review_status),
    reasonCode: asString(row.conflict_kind || row.missing_kind || row.reason_code),
  };
}

export function parseRequirementIntelligenceAnalysis(value: unknown): RequirementIntelligenceAnalysis {
  const envelope = asRecord(value);
  const data = asRecord(envelope.data || value);
  const summary = asRecord(data.summary);
  const readiness = asRecord(data.readiness);
  const rawCounts = asRecord(readiness.counts_by_type);
  const status = parseReadinessStatus(readiness.status, 'readiness.status');
  const analysisStatus = parseReadinessStatus(data.analysis_status, 'analysis_status');
  if (analysisStatus !== status) throw contractError('analysis_status/readiness.status');

  const ready = requireBoolean(readiness.ready, 'readiness.ready');
  if (ready !== (status === 'READY')) throw contractError('readiness.ready');

  const qualityClaim = requireString(readiness.quality_claim, 'readiness.quality_claim');
  if (qualityClaim !== QUALITY_CLAIM) throw contractError('readiness.quality_claim');

  const counts: Record<RequirementFindingType, number> = {
    requirement_conflict: requireNumber(rawCounts.requirement_conflict, 'readiness.counts_by_type.requirement_conflict'),
    requirement_missing: requireNumber(rawCounts.requirement_missing, 'readiness.counts_by_type.requirement_missing'),
    requirement_ambiguity: requireNumber(rawCounts.requirement_ambiguity, 'readiness.counts_by_type.requirement_ambiguity'),
  };
  const findings = asArray(data.findings).map(parseFinding);
  const findingCount = requireNumber(readiness.finding_count, 'readiness.finding_count');
  if (findingCount !== findings.length) throw contractError('readiness.finding_count/findings');
  if (Object.values(counts).reduce((total, count) => total + count, 0) !== findingCount) {
    throw contractError('readiness.counts_by_type');
  }

  return {
    schema: requireString(data.schema, 'schema'),
    productId: requireString(data.product_id, 'product_id'),
    projectId: requireString(data.project_id, 'project_id'),
    analysisStatus,
    summary: {
      sourceCount: requireNumber(summary.source_count, 'summary.source_count'),
      conflictCount: requireNumber(summary.requirement_conflict_count, 'summary.requirement_conflict_count'),
      missingCount: requireNumber(summary.requirement_missing_count, 'summary.requirement_missing_count'),
      ambiguityCount: requireNumber(summary.requirement_ambiguity_count, 'summary.requirement_ambiguity_count'),
      blockingFindingCount: requireNumber(summary.blocking_finding_count, 'summary.blocking_finding_count'),
      reviewRequiredFindingCount: requireNumber(summary.review_required_finding_count, 'summary.review_required_finding_count'),
      suppressedWithoutEvidenceCount: requireNumber(summary.suppressed_without_evidence_count, 'summary.suppressed_without_evidence_count'),
    },
    readiness: {
      status,
      ready,
      findingCount,
      blockingFindingCount: requireNumber(readiness.blocking_finding_count, 'readiness.blocking_finding_count'),
      reviewRequiredFindingCount: requireNumber(readiness.review_required_finding_count, 'readiness.review_required_finding_count'),
      blockingFindingIds: asStringArray(readiness.blocking_finding_ids),
      reviewRequiredFindingIds: asStringArray(readiness.review_required_finding_ids),
      countsByType: counts,
      qualityClaim,
    },
    findings,
  };
}

export async function getRequirementIntelligence(projectId: string): Promise<RequirementIntelligenceAnalysis | null> {
  const project = projectId.trim();
  if (!project) return null;
  const payload = await fetchJSON<unknown>(
    `${API_V1_BASE}/projects/${encodeURIComponent(project)}/requirement-intelligence`,
  );
  return parseRequirementIntelligenceAnalysis(payload);
}
