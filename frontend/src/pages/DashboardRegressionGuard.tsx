import { useSearchParams } from 'react-router-dom';
import { usePipelineData } from '../api/data';
import { Dashboard } from './Dashboard';

type JsonRecord = Record<string, unknown>;

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function asNum(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function gateLabel(gateStatus: string): string {
  if (gateStatus === 'failed') return '最近回归失败，建议阻断发布';
  if (gateStatus === 'manual_approval_required') return '最近回归需要人工复核';
  if (gateStatus === 'passed') return '最近回归通过';
  return '最近回归状态待同步';
}

function gateTone(gateStatus: string): string {
  if (gateStatus === 'failed') return 'danger';
  if (gateStatus === 'manual_approval_required') return 'warning';
  if (gateStatus === 'passed') return 'success';
  return 'neutral';
}

function DashboardRegressionBanner({ record }: { record: JsonRecord }) {
  const regressionRun = asRecord(record.regression_run);
  const regressionSummary = asRecord(record.regression_summary);
  const latestRun = asRecord(regressionSummary.latest_run);
  const gateStatus = asText(regressionRun.gate_status) || asText(latestRun.gate_status);
  const failed = asNum(regressionRun.failed_count, asNum(regressionSummary.failed_defect_count));
  const pending = asNum(regressionRun.needs_review_count, asNum(regressionSummary.pending_defect_count));
  const passed = asNum(regressionRun.passed_count, asNum(regressionSummary.passed_defect_count));
  const generatedAt = asText(regressionRun.generated_at) || asText(latestRun.generated_at);
  const ciMessage = asText(regressionRun.ci_message) || asText(regressionSummary.trend_summary) || asText(regressionSummary.headline);
  const shouldBlockFirstScreen = gateStatus === 'failed' || gateStatus === 'manual_approval_required';

  if (!shouldBlockFirstScreen) return null;

  return (
    <section className={`customer-status-card ${gateTone(gateStatus)} mb-4`}>
      <span>修复后回归 Gate</span>
      <strong>{gateLabel(gateStatus)}</strong>
      <p>{ciMessage || (gateStatus === 'failed' ? '最近一次回归仍有失败探针，不能声明缺陷已修复。' : '最近一次回归仍有需人工确认的探针，不能直接进入发布结论。')}</p>
      <div className="customer-secondary-meta">
        <span><em>通过</em><b>{passed}</b></span>
        <span><em>失败</em><b>{failed}</b></span>
        <span><em>需复核</em><b>{pending}</b></span>
        <span><em>最近执行</em><b>{generatedAt || '未上报'}</b></span>
      </div>
    </section>
  );
}

export function DashboardRegressionGuard() {
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { data } = usePipelineData(project);
  const record = asRecord(data);

  return (
    <>
      {project && <DashboardRegressionBanner record={record} />}
      <Dashboard />
    </>
  );
}

export default DashboardRegressionGuard;
