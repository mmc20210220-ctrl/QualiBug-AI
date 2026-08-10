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

  if (latestStatus === 'passed' || latestStatus === 'verified_fixed') {
    return {
      state: 'verified_fixed',
      label: '修复验证通过',
      tone: 'success',
      priority: 10,
      nextAction: 'review_release',
      nextActionLabel: '查看发布门禁',
      detail: regression.reason || '最新真实回归探针已通过；该结论只代表 QualiBug 已验证的行为恢复。',
      latestRun,
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
      detail: regression.reason || latestRun?.reason || '最新真实回归仍复现该问题，当前不能关闭验证结论。',
      latestRun,
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
      detail: regression.reason || latestRun?.reason || '本轮回归没有形成可确认的通过/失败结论，需要恢复验证条件后再次执行。',
      latestRun,
    };
  }

  return {
    state: 'pending',
    label: '等待修复后重新验证',
    tone: 'warning',
    priority: 30,
    nextAction: 'reverify_after_fix',
    nextActionLabel: '客户修复后重新验证',
    detail: regression.reason || '该 Finding 已纳入真实回归义务；客户完成修复后可直接重新验证，不需要在 QualiBug 维护研发状态。',
    latestRun,
  };
}

export function hasFindingReverificationObligation(finding: Finding): boolean {
  return Boolean(finding.regression?.included_in_suite);
}
