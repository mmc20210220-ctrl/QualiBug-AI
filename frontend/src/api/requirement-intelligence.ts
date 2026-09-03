import { asArray, asRecord, asString } from '../lib/value-guards';
import { API_V1_BASE, fetchJSON, resolveProjectId } from './session';

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

function asBoolean(value: unknown): boolean {
  return value === true;
}

function asNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function asStringArray(value: unknown): string[] {
  return asArray(value).map(asString).filter(Boolean);
}

function parseFindingType(value: unknown): RequirementFindingType | null {
  const type = asString(value);
  if (
    type === 'requirement_conflict'
    || type === 'requirement_missing'
    || type === 'requirement_ambiguity'
  ) return type;
  return null;
}

function parseReadinessStatus(value: unknown): RequirementReadinessStatus {
  const status = asString(value).toUpperCase();
  if (status === 'READY' || status === 'REVIEW_REQUIRED') return status;
  return 'NOT_READY';
}

function parseEvidence(value: unknown): RequirementEvidence[] {
  return asArray(value).map(asRecord).map((row) => ({
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
}

function parseFinding(value: unknown): RequirementFinding | null {
  const row = asRecord(value);
  const findingId = asString(row.finding_id);
  const findingType = parseFindingType(row.finding_type);
  if (!findingId || !findingType) return null;
  return {
    findingId,
    findingType,
    title: asString(row.title) || '需求审查项',
    description: asString(row.description),
    status: asString(row.status),
    severity: asString(row.severity),
    blocking: asBoolean(row.blocking),
    sourceIds: asStringArray(row.source_ids),
    evidence: parseEvidence(row.evidence),
    operatorAction: asString(row.operator_action),
    relatedObjectRefs: asStringArray(row.related_object_refs),
    relatedOperationRefs: asStringArray(row.related_operation_refs),
    reviewStatus: asString(row.review_status),
    reasonCode: asString(row.conflict_kind || row.missing_kind || row.reason_code),
  };
}

function emptyCounts(): Record<RequirementFindingType, number> {
  return {
    requirement_conflict: 0,
    requirement_missing: 0,
    requirement_ambiguity: 0,
  };
}

export function parseRequirementIntelligenceAnalysis(value: unknown): RequirementIntelligenceAnalysis {
  const envelope = asRecord(value);
  const data = asRecord(envelope.data || value);
  const summary = asRecord(data.summary);
  const readiness = asRecord(data.readiness);
  const rawCounts = asRecord(readiness.counts_by_type);
  const counts = emptyCounts();
  counts.requirement_conflict = asNumber(rawCounts.requirement_conflict);
  counts.requirement_missing = asNumber(rawCounts.requirement_missing);
  counts.requirement_ambiguity = asNumber(rawCounts.requirement_ambiguity);
  const findings = asArray(data.findings).map(parseFinding).filter((item): item is RequirementFinding => item !== null);
  const status = parseReadinessStatus(readiness.status || data.analysis_status);

  return {
    schema: asString(data.schema),
    productId: asString(data.product_id) || 'requirement_intelligence',
    projectId: asString(data.project_id),
    analysisStatus: status,
    summary: {
      sourceCount: asNumber(summary.source_count),
      conflictCount: asNumber(summary.requirement_conflict_count),
      missingCount: asNumber(summary.requirement_missing_count),
      ambiguityCount: asNumber(summary.requirement_ambiguity_count),
      blockingFindingCount: asNumber(summary.blocking_finding_count),
      reviewRequiredFindingCount: asNumber(summary.review_required_finding_count),
      suppressedWithoutEvidenceCount: asNumber(summary.suppressed_without_evidence_count),
    },
    readiness: {
      status,
      ready: asBoolean(readiness.ready),
      findingCount: asNumber(readiness.finding_count),
      blockingFindingCount: asNumber(readiness.blocking_finding_count),
      reviewRequiredFindingCount: asNumber(readiness.review_required_finding_count),
      blockingFindingIds: asStringArray(readiness.blocking_finding_ids),
      reviewRequiredFindingIds: asStringArray(readiness.review_required_finding_ids),
      countsByType: counts,
      qualityClaim: asString(readiness.quality_claim),
    },
    findings,
  };
}

export async function getRequirementIntelligence(projectId: string): Promise<RequirementIntelligenceAnalysis | null> {
  const project = await resolveProjectId(projectId);
  if (!project) return null;
  const payload = await fetchJSON<unknown>(
    `${API_V1_BASE}/projects/${encodeURIComponent(project)}/requirement-intelligence`,
  );
  return parseRequirementIntelligenceAnalysis(payload);
}
