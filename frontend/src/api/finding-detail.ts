import { useCallback, useEffect, useState } from 'react';
import { asRecord, asString } from '../lib/value-guards';
import type { Finding } from '../types';
import { API_V1_BASE, ApiError, fetchJSON, resolveProjectId } from './session';

type JsonRecord = Record<string, unknown>;
type ScanCompletedDetail = { project: string };

const SCAN_COMPLETED_EVENT = 'qualibug:scan-completed';

function stripFixAdviceForCustomer(value: Finding): Finding {
  const sanitized = { ...(value as unknown as JsonRecord) };
  delete sanitized.recommended_fix;
  const technical = asRecord(sanitized.technical_details);
  if (Object.keys(technical).length > 0) {
    const sanitizedTechnical = { ...technical };
    delete sanitizedTechnical.recommended_fix;
    delete sanitizedTechnical.possible_root_cause;
    sanitized.technical_details = sanitizedTechnical;
  }
  sanitized.product_responsibility_boundary = {
    scope: 'defect_discovery_evidence_regression_release_status',
    no_fix_advice: true,
    customer_meaning: 'QualiBug-AI 只提供缺陷事实、证据链、修复后回归验证和发布状态，不提供修复建议、修复方案或修复代码。',
  };
  return sanitized as unknown as Finding;
}

function findingFrom(value: unknown): Finding | null {
  const record = asRecord(value);
  return asString(record.id) || asString(record.title)
    ? stripFixAdviceForCustomer(record as unknown as Finding)
    : null;
}

export async function getFinding(projectId: string, findingId: string): Promise<Finding | null> {
  const resolvedProjectId = await resolveProjectId(projectId);
  const normalizedFindingId = findingId.trim();
  if (!resolvedProjectId || !normalizedFindingId) return null;
  try {
    const envelope = asRecord(
      await fetchJSON<unknown>(
        `${API_V1_BASE}/projects/${encodeURIComponent(resolvedProjectId)}/findings/${encodeURIComponent(normalizedFindingId)}`,
      ),
    );
    return findingFrom(envelope.data);
  } catch (error: unknown) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export function useFindingDetail(project: string, findingId: string) {
  const [finding, setFinding] = useState<Finding | null>(null);
  const [loading, setLoading] = useState(Boolean(project && findingId));
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!project || !findingId.trim()) {
      setFinding(null);
      setLoading(false);
      setError('');
      return;
    }
    setLoading(true);
    setError('');
    try {
      setFinding(await getFinding(project, findingId));
    } catch (caught: unknown) {
      setFinding(null);
      setError(caught instanceof Error ? caught.message : '问题详情加载失败');
    } finally {
      setLoading(false);
    }
  }, [project, findingId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!project || typeof window === 'undefined') return undefined;
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<ScanCompletedDetail>).detail;
      if (detail?.project === project) void load();
    };
    window.addEventListener(SCAN_COMPLETED_EVENT, handler);
    return () => window.removeEventListener(SCAN_COMPLETED_EVENT, handler);
  }, [project, load]);

  return { finding, loading, error, refetch: load };
}
