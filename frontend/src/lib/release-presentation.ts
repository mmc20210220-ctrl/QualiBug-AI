export type ReleasePresentationTone = 'red' | 'yellow' | 'green';

export type ReleasePresentationCheck = {
  name?: string;
  status?: string;
  detail?: string;
};

export type ReleasePresentationInput = {
  p0Count: number;
  confirmedDefectCount: number;
  pipelineHealthStatus?: string;
  campaignStatus?: string;
  gateOverall?: string;
  gateChecks?: ReleasePresentationCheck[];
  hasGateData?: boolean;
  regressionGateStatus?: string;
};

export type ReleasePresentation = {
  color: ReleasePresentationTone;
  label: string;
  advice: string;
  incomplete: boolean;
  blockingCheckCount: number;
  pendingCheckCount: number;
};

function normalized(value: unknown): string {
  return String(value || '').trim().toLowerCase();
}

function isCampaignCoverageCheck(check: ReleasePresentationCheck): boolean {
  const name = normalized(check.name);
  return name.includes('campaign') || name.includes('覆盖') || name.includes('治理状态');
}

/**
 * Customer-facing release presentation only.
 *
 * This helper never recomputes backend quality facts. It only resolves display
 * priority when multiple already-reported facts coexist across Dashboard and
 * ReleaseGate. Known blockers must never be hidden by an incomplete scan, while
 * an incomplete scan by itself must never be painted green.
 */
export function deriveReleasePresentation(input: ReleasePresentationInput): ReleasePresentation {
  const p0Count = Math.max(0, Number(input.p0Count) || 0);
  const confirmedDefectCount = Math.max(0, Number(input.confirmedDefectCount) || 0);
  const pipelineHealthStatus = normalized(input.pipelineHealthStatus);
  const campaignStatus = normalized(input.campaignStatus);
  const gateOverall = normalized(input.gateOverall);
  const regressionGateStatus = normalized(input.regressionGateStatus);
  const gateChecks = Array.isArray(input.gateChecks) ? input.gateChecks : [];
  const failingChecks = gateChecks.filter((check) => normalized(check.status) === 'fail');
  const pendingChecks = gateChecks.filter((check) => normalized(check.status) === 'pending');
  const incomplete = ['failed_safe', 'blocked'].includes(pipelineHealthStatus)
    || campaignStatus === 'blocked'
    || campaignStatus === 'coverage_deferred';
  const gateFailureOnlyExplainsIncomplete = failingChecks.length > 0
    && failingChecks.every(isCampaignCoverageCheck);
  const hasIndependentGateFailure = gateOverall === 'fail'
    && (!incomplete || !gateFailureOnlyExplainsIncomplete || failingChecks.length === 0);
  const regressionFailed = regressionGateStatus === 'failed';
  const regressionPending = ['pending', 'not_ready', 'manual_approval_required'].includes(regressionGateStatus);

  if (p0Count > 0) {
    return {
      color: 'red',
      label: '建议阻断',
      advice: `${p0Count} 个已确认 P0 需优先修复；即使本轮覆盖尚未完整，也不能降低已知阻断风险。`,
      incomplete,
      blockingCheckCount: failingChecks.length,
      pendingCheckCount: pendingChecks.length,
    };
  }

  if (regressionFailed) {
    return {
      color: 'red',
      label: '不建议发布',
      advice: '最新回归门禁已明确失败，请先处理回归失败项并重新验证。',
      incomplete,
      blockingCheckCount: failingChecks.length,
      pendingCheckCount: pendingChecks.length,
    };
  }

  if (hasIndependentGateFailure) {
    return {
      color: 'red',
      label: '不建议发布',
      advice: failingChecks.length > 0
        ? `${failingChecks.length} 个发布门禁检查已明确失败，请先处理阻断项。`
        : '发布门禁已明确返回失败，请先处理阻断项。',
      incomplete,
      blockingCheckCount: failingChecks.length,
      pendingCheckCount: pendingChecks.length,
    };
  }

  if (incomplete) {
    return {
      color: 'yellow',
      label: '待确认',
      advice: campaignStatus === 'coverage_deferred'
        ? '本轮仍有明确未覆盖范围，当前结果不足以形成完整发布结论。'
        : '检测流程尚未完整结束，当前结果不足以形成发布结论。',
      incomplete: true,
      blockingCheckCount: failingChecks.length,
      pendingCheckCount: pendingChecks.length,
    };
  }

  if (regressionPending) {
    return {
      color: 'yellow',
      label: '待处理',
      advice: '回归门禁尚未完成或仍需人工确认，完成回归闭环后再决定是否发布。',
      incomplete: false,
      blockingCheckCount: failingChecks.length,
      pendingCheckCount: pendingChecks.length,
    };
  }

  if (gateOverall === 'pending' || pendingChecks.length > 0) {
    return {
      color: 'yellow',
      label: '待处理',
      advice: `${Math.max(pendingChecks.length, 1)} 个发布门禁事项仍待处理，完成后再决定是否发布。`,
      incomplete: false,
      blockingCheckCount: failingChecks.length,
      pendingCheckCount: pendingChecks.length,
    };
  }

  if (gateOverall === 'pass' && input.hasGateData !== false) {
    return {
      color: 'green',
      label: '可以发布',
      advice: '当前发布门禁已通过；仍应以本轮已上报范围、最新回归状态和商业交付守卫为边界。',
      incomplete: false,
      blockingCheckCount: failingChecks.length,
      pendingCheckCount: pendingChecks.length,
    };
  }

  if (confirmedDefectCount > 0) {
    return {
      color: 'yellow',
      label: '有条件发布',
      advice: `${confirmedDefectCount} 个已确认问题需要评估；发布门禁尚未提供完整放行结论。`,
      incomplete: false,
      blockingCheckCount: failingChecks.length,
      pendingCheckCount: pendingChecks.length,
    };
  }

  return {
    color: 'yellow',
    label: '待确认',
    advice: '尚未取得完整发布门禁回执，不能仅凭 0 个已确认问题推导为可以发布。',
    incomplete: false,
    blockingCheckCount: failingChecks.length,
    pendingCheckCount: pendingChecks.length,
  };
}
