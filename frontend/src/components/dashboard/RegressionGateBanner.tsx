import { useSearchParams } from 'react-router-dom';
import { asArray, asNum, asRecord, asText, type JsonRecord } from '../../lib/value-guards';
import { DashboardVerificationDeltaPanel } from './DashboardVerificationDeltaPanel';

function releaseTone(overallStatus: string): string {
  if (overallStatus === 'fail') return 'danger';
  if (overallStatus === 'pending') return 'warning';
  return 'neutral';
}

function releaseLabel(overallStatus: string): string {
  if (overallStatus === 'fail') return '发布门禁阻塞，不能直接发布';
  if (overallStatus === 'pending') return '发布门禁待确认，需先完成验证或复核';
  return '发布门禁状态待同步';
}

function gateLabel(gateStatus: string, hasPendingObligation: boolean): string {
  if (gateStatus === 'failed') return '最近修复后验证失败，建议阻断发布';
  if (gateStatus === 'manual_approval_required') return '最近修复后验证需要人工复核';
  if (gateStatus === 'passed') return '最近修复后验证通过';
  if (hasPendingObligation) return '已生成验证义务，尚未执行修复后验证';
  return '最近验证状态待同步';
}

function gateTone(gateStatus: string, hasPendingObligation: boolean): string {
  if (gateStatus === 'failed') return 'danger';
  if (gateStatus === 'manual_approval_required' || hasPendingObligation) return 'warning';
  if (gateStatus === 'passed') return 'success';
  return 'neutral';
}

/**
 * Dashboard 首屏的两类独立事实：
 * 1) DashboardVerificationDeltaPanel：最新真实验证对 Finding 结论造成了什么变化；
 * 2) Gate banner：这些事实对发布门禁意味着什么。
 *
 * 二者不能互相替代。验证变化不是发布结论，发布 Gate 也不能伪造逐 Finding 变化。
 */
export function RegressionGateBanner({ record }: { record: JsonRecord }) {
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const deltaPanel = <DashboardVerificationDeltaPanel record={record} project={project} />;

  const releaseGate = asRecord(record.release_gate);
  const releaseOverall = asText(releaseGate.overall_status);
  const releaseChecks = asArray(releaseGate.checks).map(asRecord);
  const blockingCount = asNum(releaseGate.blocking_check_count, releaseChecks.filter((item) => asText(item.status) === 'fail').length);
  const pendingCount = asNum(releaseGate.pending_check_count, releaseChecks.filter((item) => asText(item.status) === 'pending').length);
  const regressionGateCheck = releaseChecks.find((item) => asText(item.name) === '修复后回归 Gate') || releaseChecks[0];

  if (releaseOverall === 'fail' || releaseOverall === 'pending') {
    return (
      <>
        {deltaPanel}
        <section className={`customer-status-card ${releaseTone(releaseOverall)} mb-4`}>
          <span>发布门禁 Gate</span>
          <strong>{releaseLabel(releaseOverall)}</strong>
          <p>{asText(regressionGateCheck?.detail) || '后端 release_gate 合同显示当前仍存在发布阻断或待确认项。'}</p>
          <div className="customer-secondary-meta">
            <span><em>阻塞项</em><b>{blockingCount}</b></span>
            <span><em>待确认</em><b>{pendingCount}</b></span>
            <span><em>门禁来源</em><b>{asText(releaseGate.source) || 'release_gate'}</b></span>
            <span><em>首要检查</em><b>{asText(regressionGateCheck?.name) || '发布门禁'}</b></span>
          </div>
        </section>
      </>
    );
  }

  const regressionRun = asRecord(record.regression_run);
  const regressionSummary = asRecord(record.regression_summary);
  const latestRun = asRecord(regressionSummary.latest_run);
  const regressionRefresh = asRecord(record.regression_suite_refresh);
  const regressionSuite = asRecord(record.regression_suite);
  const refreshSummary = asRecord(regressionRefresh.summary);
  const gateStatus = asText(regressionRun.gate_status) || asText(latestRun.gate_status);
  const hasLatestRun = Boolean(gateStatus || asText(regressionRun.status) || asText(latestRun.generated_at));
  const obligationCount = asNum(regressionSuite.total_probe_count, asNum(refreshSummary.total_probe_count));
  const confirmedLedgerProbeCount = asNum(regressionSuite.confirmed_ledger_probe_count, asNum(refreshSummary.confirmed_ledger_probe_count));
  const hasPendingObligation = !hasLatestRun && asText(regressionRefresh.status) === 'refreshed' && obligationCount > 0;
  const failed = asNum(regressionRun.failed_count, asNum(regressionSummary.failed_defect_count));
  const pending = asNum(regressionRun.needs_review_count, asNum(regressionSummary.pending_defect_count, hasPendingObligation ? obligationCount : 0));
  const passed = asNum(regressionRun.passed_count, asNum(regressionSummary.passed_defect_count));
  const generatedAt = asText(regressionRun.generated_at) || asText(latestRun.generated_at);
  const ciMessage = asText(regressionRun.ci_message) || asText(regressionSummary.trend_summary) || asText(regressionSummary.headline);
  const shouldBlockFirstScreen = gateStatus === 'failed' || gateStatus === 'manual_approval_required' || hasPendingObligation;

  if (!shouldBlockFirstScreen) return deltaPanel;

  return (
    <>
      {deltaPanel}
      <section className={`customer-status-card ${gateTone(gateStatus, hasPendingObligation)} mb-4`}>
        <span>修复后验证 Gate</span>
        <strong>{gateLabel(gateStatus, hasPendingObligation)}</strong>
        <p>{ciMessage || (hasPendingObligation ? '扫描后已经形成 confirmed bug 的真实验证义务，但尚未执行；发布前需要先完成 Smoke 或 Release 修复后验证。' : gateStatus === 'failed' ? '最近一次修复后验证仍有失败探针，不能声明缺陷已修复。' : '最近一次修复后验证仍有需人工确认的探针，不能直接进入发布结论。')}</p>
        <div className="customer-secondary-meta">
          <span><em>通过</em><b>{passed}</b></span>
          <span><em>失败</em><b>{failed}</b></span>
          <span><em>{hasPendingObligation ? '待验证' : '需复核'}</em><b>{pending}</b></span>
          <span><em>验证义务</em><b>{obligationCount}</b></span>
          <span><em>确认缺陷台账</em><b>{confirmedLedgerProbeCount}</b></span>
          <span><em>最近执行</em><b>{generatedAt || '未执行'}</b></span>
        </div>
      </section>
    </>
  );
}
