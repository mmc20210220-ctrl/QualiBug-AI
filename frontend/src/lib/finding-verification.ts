import type { Finding } from '../types';

type RegressionHistoryItem = NonNullable<Finding['regression']>['history'][number];

export type FindingVerificationState =
  | 'verified_fixed'
  | 'still_failing'
  | 'inconclusive'
  | 'pending'
  | 'not_enrolled';

export type FindingVerificationNextAction =
  | 'review_failure'
  | 'restore_verification'
  | 'reverify_after_fix'
  | 'review_release'
  | 'none';

export type FindingVerificationPresentation = {
  state: FindingVerificationState;
  label: string;
  tone: 'success' | 'danger' | 'warning' | 'neutral';
  priority: number;
  nextAction: FindingVerificationNextAction;
  nextActionLabel: string;
  detail: string;
  latestRun: RegressionHistoryItem | null;
};

export type FindingVerificationOutcome = 'fixed' | 'open' | 'unknown';

export type VerificationRunPresentation = {
  state: Exclude<FindingVerificationState, 'not_enrolled'>;
  label: string;
  tone: 'success' | 'danger' | 'warning';
  priority: number;
  nextAction: Exclude<FindingVerificationNextAction, 'none'>;
  nextActionLabel: string;
  detail: string;
  outcome: FindingVerificationOutcome;
};

export type FindingVerificationTimelineEvent = {
  kind: 'baseline' | 'verification';
  key: string;
  generatedAt: string;
  label: string;
  tone: 'success' | 'danger' | 'warning' | 'neutral';
  detail: string;
  outcome: FindingVerificationOutcome;
  changedConclusion: boolean;
  transitionLabel: string;
  run: RegressionHistoryItem | null;
};

export type LatestVerificationRunFinding = {
  finding: Finding;
  event: FindingVerificationTimelineEvent;
};

export type LatestVerificationRunSummary = {
  runAt: string;
  matchedCount: number;
  changedCount: number;
  fixedCount: number;
  reopenedCount: number;
  stillFailingCount: number;
  inconclusiveCount: number;
  keptFixedCount: number;
  rows: LatestVerificationRunFinding[];
};

export type FocusedVerificationRunSummary = {
  generatedAt: string;
  previousKnownOutcome: Exclude<FindingVerificationOutcome, 'unknown'>;
  previousKnownLabel: string;
  currentOutcome: FindingVerificationOutcome;
  currentLabel: string;
  changedConclusion: boolean;
  transitionLabel: string;
  releaseMeaning: string;
  event: FindingVerificationTimelineEvent;
};

const INCONCLUSIVE_STATUSES = new Set([
  'blocked',
  'error',
  'failed_safe',
  'indeterminate',
  'needs_review',
  'not_executed',
  'not_ready',
  'skipped',
  'unverifiable',
  'unknown',
]);

function latestHistory(finding: Finding): RegressionHistoryItem | null {
  const history = finding.regression?.history || [];
  if (history.length === 0) return null;
  return [...history].sort((left, right) => String(right.generated_at || '').localeCompare(String(left.generated_at || '')))[0] || null;
}

export function deriveVerificationRunPresentation(
  rawStatus: unknown,
  rawGateStatus: unknown,
  reason = '',
): VerificationRunPresentation {
  const latestStatus = String(rawStatus || '').trim().toLowerCase();
  const gateStatus = String(rawGateStatus || '').trim().toLowerCase();

  if (latestStatus === 'passed' || latestStatus === 'verified_fixed') {
    return {
      state: 'verified_fixed',
      label: '修复验证通过',
      tone: 'success',
      priority: 10,
      nextAction: 'review_release',
      nextActionLabel: '查看发布门禁',
      detail: reason || '该轮真实回归探针已通过；该结论只代表 QualiBug 已验证的行为恢复。',
      outcome: 'fixed',
    };
  }

  if (latestStatus === 'failed' || latestStatus === 'reopened' || gateStatus === 'failed') {
    return {
      state: 'still_failing',
      label: '重新验证仍失败',
      tone: 'danger',
      priority: 50,
      nextAction: 'review_failure',
      nextActionLabel: '查看失败证据',
      detail: reason || '该轮真实回归仍复现该问题，当前不能关闭验证结论。',
      outcome: 'open',
    };
  }

  if (INCONCLUSIVE_STATUSES.has(latestStatus) || INCONCLUSIVE_STATUSES.has(gateStatus)) {
    return {
      state: 'inconclusive',
      label: '本轮无法确认修复',
      tone: 'warning',
      priority: 40,
      nextAction: 'restore_verification',
      nextActionLabel: '恢复验证条件后重试',
      detail: reason || '该轮回归没有形成可确认的通过/失败结论，需要恢复验证条件后再次执行。',
      outcome: 'unknown',
    };
  }

  return {
    state: 'pending',
    label: '等待修复后重新验证',
    tone: 'warning',
    priority: 30,
    nextAction: 'reverify_after_fix',
    nextActionLabel: '客户修复后重新验证',
    detail: reason || '该轮回执尚未形成终态验证结果。',
    outcome: 'unknown',
  };
}

export function deriveFindingVerification(finding: Finding): FindingVerificationPresentation {
  const regression = finding.regression;
  const latestRun = latestHistory(finding);
  if (!regression?.included_in_suite) {
    return {
      state: 'not_enrolled',
      label: '暂无可执行重新验证',
      tone: 'neutral',
      priority: 20,
      nextAction: 'none',
      nextActionLabel: '查看验证依据',
      detail: '当前 Finding 没有真实回归义务，前端不会构造 synthetic probe 或提交空验证。',
      latestRun,
    };
  }

  const latestStatus = String(regression.latest_status || latestRun?.status || '').trim().toLowerCase();
  const gateStatus = String(regression.gate_status || latestRun?.gate_status || '').trim().toLowerCase();
  const runPresentation = deriveVerificationRunPresentation(
    latestStatus,
    gateStatus,
    regression.reason || latestRun?.reason || '',
  );

  return {
    ...runPresentation,
    detail: runPresentation.detail || regression.reason || latestRun?.reason || '',
    latestRun,
  };
}

function isKnownVerificationOutcome(
  outcome: FindingVerificationOutcome,
): outcome is Exclude<FindingVerificationOutcome, 'unknown'> {
  return outcome === 'fixed' || outcome === 'open';
}

function outcomeLabel(outcome: FindingVerificationOutcome): string {
  if (outcome === 'fixed') return '修复验证通过';
  if (outcome === 'open') return '问题仍成立';
  return '结论待确认';
}

export function buildFindingVerificationTimeline(finding: Finding): FindingVerificationTimelineEvent[] {
  const timeline: FindingVerificationTimelineEvent[] = [{
    kind: 'baseline',
    key: `baseline:${finding.id}`,
    generatedAt: finding.timestamp || '',
    label: '原始问题已确认',
    tone: 'danger',
    detail: finding.actual || finding.business_summary || finding.business_impact?.summary || '该 Finding 已形成真实问题结论。',
    outcome: 'open',
    changedConclusion: false,
    transitionLabel: '验证基线',
    run: null,
  }];

  const history = [...(finding.regression?.history || [])]
    .sort((left, right) => String(left.generated_at || '').localeCompare(String(right.generated_at || '')));
  let lastKnownOutcome: Exclude<FindingVerificationOutcome, 'unknown'> = 'open';

  history.forEach((run, index) => {
    const presentation = deriveVerificationRunPresentation(run.status, run.gate_status, run.reason || '');
    const isKnownOutcome = isKnownVerificationOutcome(presentation.outcome);
    const changedConclusion = isKnownOutcome && presentation.outcome !== lastKnownOutcome;
    const previousOutcome = lastKnownOutcome;
    const transitionLabel = changedConclusion
      ? `${outcomeLabel(previousOutcome)} → ${outcomeLabel(presentation.outcome)}`
      : presentation.outcome === 'unknown'
        ? '本轮未形成可确认结论'
        : presentation.outcome === 'fixed'
          ? '修复结论保持通过'
          : '问题结论仍成立';

    timeline.push({
      kind: 'verification',
      key: `${run.generated_at || 'run'}:${run.regression_probe_id || index}`,
      generatedAt: run.generated_at || '',
      label: presentation.label,
      tone: presentation.tone,
      detail: run.reason || run.ci_message || presentation.detail,
      outcome: presentation.outcome,
      changedConclusion,
      transitionLabel,
      run,
    });

    if (isKnownVerificationOutcome(presentation.outcome)) {
      lastKnownOutcome = presentation.outcome;
    }
  });

  return timeline;
}

export function deriveFocusedVerificationRunSummary(
  finding: Finding,
  generatedAt: string,
): FocusedVerificationRunSummary | null {
  const normalizedGeneratedAt = String(generatedAt || '').trim();
  if (!normalizedGeneratedAt) return null;

  const timeline = buildFindingVerificationTimeline(finding);
  const eventIndex = timeline.findIndex(
    (event) => event.kind === 'verification' && event.generatedAt === normalizedGeneratedAt,
  );
  if (eventIndex < 0) return null;

  const event = timeline[eventIndex];
  let previousKnownOutcome: Exclude<FindingVerificationOutcome, 'unknown'> = 'open';
  for (let index = eventIndex - 1; index >= 0; index -= 1) {
    const outcome = timeline[index].outcome;
    if (outcome === 'fixed' || outcome === 'open') {
      previousKnownOutcome = outcome;
      break;
    }
  }

  const releaseMeaning = event.outcome === 'open'
    ? '该 Finding 在这一轮仍是已知验证风险；是否阻断发布仍由项目级 Release Gate 判定。'
    : event.outcome === 'fixed'
      ? '该 Finding 在这一轮已验证恢复，但单条问题通过不等于项目可以发布。'
      : '这一轮不能证明该 Finding 已修复，也不能作为放行依据；项目级 Release Gate 仍需其他真实事实。';

  return {
    generatedAt: normalizedGeneratedAt,
    previousKnownOutcome,
    previousKnownLabel: outcomeLabel(previousKnownOutcome),
    currentOutcome: event.outcome,
    currentLabel: event.label,
    changedConclusion: event.changedConclusion,
    transitionLabel: event.transitionLabel,
    releaseMeaning,
    event,
  };
}

export function latestFindingConclusionChange(finding: Finding): FindingVerificationTimelineEvent | null {
  const changed = buildFindingVerificationTimeline(finding).filter((event) => event.changedConclusion);
  return changed[changed.length - 1] || null;
}

export function deriveLatestVerificationRunSummary(
  findings: Finding[],
  runAt: string,
): LatestVerificationRunSummary | null {
  const normalizedRunAt = String(runAt || '').trim();
  if (!normalizedRunAt) return null;

  const rows: LatestVerificationRunFinding[] = [];
  for (const finding of findings) {
    const event = buildFindingVerificationTimeline(finding).find(
      (item) => item.kind === 'verification' && item.generatedAt === normalizedRunAt,
    );
    if (event) rows.push({ finding, event });
  }

  const fixedCount = rows.filter(({ event }) => event.changedConclusion && event.outcome === 'fixed').length;
  const reopenedCount = rows.filter(({ event }) => event.changedConclusion && event.outcome === 'open').length;
  const stillFailingCount = rows.filter(({ event }) => !event.changedConclusion && event.outcome === 'open').length;
  const inconclusiveCount = rows.filter(({ event }) => event.outcome === 'unknown').length;
  const keptFixedCount = rows.filter(({ event }) => !event.changedConclusion && event.outcome === 'fixed').length;

  return {
    runAt: normalizedRunAt,
    matchedCount: rows.length,
    changedCount: fixedCount + reopenedCount,
    fixedCount,
    reopenedCount,
    stillFailingCount,
    inconclusiveCount,
    keptFixedCount,
    rows,
  };
}

export function hasFindingReverificationObligation(finding: Finding): boolean {
  return Boolean(finding.regression?.included_in_suite);
}
