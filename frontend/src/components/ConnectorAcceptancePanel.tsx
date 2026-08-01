import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  getConnectorAcceptanceJob,
  getConnectorAcceptanceReport,
  listConnectorAcceptanceReports,
  startConnectorAcceptance,
  type ConnectorAcceptanceJob,
  type ConnectorAcceptanceReport,
  type ConnectorAcceptanceReportSummary,
} from '../api/connector-acceptance';
import { useToast } from './useToast';
import './ConnectorAcceptancePanel.css';

type ConnectorAcceptancePanelProps = {
  projectId: string;
  connectorId: string;
  disabled?: boolean;
};

function formatTime(value?: string): string {
  if (!value) return '尚未运行';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', { hour12: false });
}

function percent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value * 100)));
}

function isActiveJob(job: ConnectorAcceptanceJob | null): boolean {
  return job?.status === 'PENDING' || job?.status === 'RUNNING';
}

function checkLabel(checkId: string): string {
  const labels: Record<string, string> = {
    CONNECTION_AVAILABLE: '飞书连接可用',
    REMOTE_ACCESS_READ_ONLY: '客户资料访问只读',
    TENANT_SCALE_MEETS_PROFILE: '资料规模达到 Pilot 门槛',
    KNOWLEDGE_COVERAGE_MEETS_PROFILE: '知识覆盖率达到 Pilot 门槛',
    UNSUPPORTED_RATIO_WITHIN_PROFILE: '不支持资料比例在门槛内',
    ACCEPTANCE_REQUIRED_RUNS_COMPLETED: '完成规定轮次',
  };
  if (labels[checkId]) return labels[checkId];
  if (checkId.endsWith('_NO_UNKNOWN_GAPS')) return '没有未知资料缺口';
  if (checkId.endsWith('_NO_CUSTOMER_MUTATION')) return '没有修改客户资料';
  if (checkId.endsWith('_STABLE_SNAPSHOT_NOT_REEXPORTED')) return '稳定快照未重复导出';
  if (checkId.endsWith('_DURATION_WITHIN_LIMIT')) return '同步耗时在门槛内';
  if (checkId.endsWith('_CHECKPOINT_COMMITTED')) return '同步检查点完整提交';
  if (checkId.endsWith('_COMPLETE')) return '同步完整完成';
  return checkId;
}

export function ConnectorAcceptancePanel({
  projectId,
  connectorId,
  disabled = false,
}: ConnectorAcceptancePanelProps) {
  const toast = useToast();
  const [latest, setLatest] = useState<ConnectorAcceptanceReportSummary | null>(null);
  const [report, setReport] = useState<ConnectorAcceptanceReport | null>(null);
  const [job, setJob] = useState<ConnectorAcceptanceJob | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const pollInFlightRef = useRef(false);
  const notifiedJobRef = useRef('');

  const loadReport = useCallback(async (reportId: string) => {
    const loaded = await getConnectorAcceptanceReport(projectId, connectorId, reportId);
    setLatest(loaded);
    setReport(loaded);
    return loaded;
  }, [projectId, connectorId]);

  const load = useCallback(async () => {
    if (!projectId || !connectorId) return;
    setLoading(true);
    setError('');
    try {
      const [inventory, currentJob] = await Promise.all([
        listConnectorAcceptanceReports(projectId, connectorId),
        getConnectorAcceptanceJob(projectId, connectorId),
      ]);
      setJob(currentJob);
      setRunning(isActiveJob(currentJob));
      const nextLatest = inventory.reports[0] || null;
      setLatest(nextLatest);
      const reportId = currentJob.report_id || nextLatest?.report_id;
      if (reportId) {
        await loadReport(reportId);
      } else {
        setReport(null);
      }
      if (currentJob.status === 'FAILED' || currentJob.status === 'INTERRUPTED') {
        setError('上一次验收任务未完整结束，可重新运行 Pilot 验收。');
      }
    } catch (loadError: unknown) {
      setError(loadError instanceof Error ? loadError.message : '验收状态加载失败。');
    } finally {
      setLoading(false);
    }
  }, [projectId, connectorId, loadReport]);

  useEffect(() => {
    void load();
  }, [load]);

  const activeJobId = job?.job_id;
  const activeJobStatus = job?.status;
  const activeJob = activeJobStatus === 'PENDING' || activeJobStatus === 'RUNNING';

  useEffect(() => {
    if (!activeJobId || !activeJob) return undefined;
    let cancelled = false;

    const notifyOnce = (jobId: string, message: string, tone: 'success' | 'warning') => {
      if (notifiedJobRef.current === jobId) return;
      notifiedJobRef.current = jobId;
      toast.show(message, tone);
    };

    const poll = async () => {
      if (pollInFlightRef.current) return;
      pollInFlightRef.current = true;
      try {
        const next = await getConnectorAcceptanceJob(projectId, connectorId, activeJobId);
        if (cancelled) return;
        setJob(next);
        setRunning(isActiveJob(next));
        if (next.status === 'COMPLETE' && next.report_id) {
          const completed = await loadReport(next.report_id);
          if (cancelled) return;
          setError('');
          if (completed.acceptance_ready) {
            notifyOnce(next.job_id, '飞书真实租户 Pilot 验收已通过。', 'success');
          } else {
            notifyOnce(
              next.job_id,
              `验收完成，发现 ${completed.summary.blocker_failure_count} 个阻断项。`,
              'warning',
            );
          }
        } else if (next.status === 'FAILED' || next.status === 'INTERRUPTED') {
          setError('验收任务未完整结束，已有资料不受影响，可重新运行。');
          notifyOnce(next.job_id, '验收任务未完整结束，已有资料不受影响。', 'warning');
        } else {
          setError('');
        }
      } catch (pollError: unknown) {
        if (cancelled) return;
        setError(pollError instanceof Error ? pollError.message : '验收任务状态读取失败。');
      } finally {
        pollInFlightRef.current = false;
      }
    };

    const timer = window.setInterval(() => void poll(), 2000);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeJobId, activeJob, projectId, connectorId, loadReport, toast]);

  const blockers = useMemo(
    () => (report?.checks || []).filter((check) => check.severity === 'BLOCKER' && check.status === 'FAIL'),
    [report],
  );

  const runPilot = async () => {
    setRunning(true);
    setError('');
    try {
      const started = await startConnectorAcceptance(projectId, connectorId, 'pilot');
      notifiedJobRef.current = '';
      setJob(started);
      setRunning(isActiveJob(started));
      toast.show('Pilot 验收任务已启动，可刷新页面后继续查看进度。', 'success');
    } catch (runError: unknown) {
      const message = runError instanceof Error ? runError.message : '验收任务启动失败。';
      setRunning(false);
      setError(message);
      toast.show(message, 'danger');
    }
  };

  const active = isActiveJob(job) || running;
  const interrupted = job?.status === 'FAILED' || job?.status === 'INTERRUPTED';
  const tone = active ? 'running' : interrupted ? 'fail' : latest?.acceptance_ready ? 'pass' : latest ? 'fail' : 'idle';
  const status = active
    ? '验收运行中'
    : interrupted
      ? '任务未完整结束'
      : latest?.acceptance_ready
        ? '已通过'
        : latest
          ? '未通过'
          : '尚未验收';
  const coverage = percent(latest?.summary.minimum_coverage_ratio || 0);

  return (
    <section className={`connector-acceptance connector-acceptance-${tone}`} aria-label="飞书真实租户验收">
      <div className="connector-acceptance-heading">
        <div>
          <span>真实租户验收</span>
          <strong>{loading ? '读取中…' : status}</strong>
        </div>
        <button
          className="btn btn-secondary"
          type="button"
          onClick={() => void runPilot()}
          disabled={disabled || loading || active}
        >
          {active ? '正在后台执行两轮验收…' : latest ? '重新运行 Pilot 验收' : '运行 Pilot 验收'}
        </button>
      </div>

      <p className="connector-acceptance-note">
        连续执行两轮只读同步，验证连接、覆盖率、增量复用、检查点和客户资料非修改边界。
        验收固定使用 RETAIN，不删除或修改飞书原资料。
      </p>

      {active && (
        <div className="connector-acceptance-running-note">
          任务在服务端持续运行，关闭或刷新页面不会中断；页面会自动恢复并查询最新状态。
        </div>
      )}

      {error && <div className="connector-acceptance-error">{error}</div>}

      {latest && (
        <div className="connector-acceptance-metrics">
          <div>
            <span>最低覆盖率</span>
            <strong>{coverage}%</strong>
          </div>
          <div>
            <span>最大资料数</span>
            <strong>{latest.summary.maximum_discovered_resource_count}</strong>
          </div>
          <div>
            <span>完成轮次</span>
            <strong>{latest.summary.executed_run_count}/{latest.summary.required_run_count}</strong>
          </div>
          <div>
            <span>阻断项</span>
            <strong>{latest.summary.blocker_failure_count}</strong>
          </div>
        </div>
      )}

      {latest && (
        <div className="connector-acceptance-meta">
          <span>{active ? '上次报告等级' : '验收等级'}：{latest.profile || 'pilot'}</span>
          <span>最近完成：{formatTime(latest.completed_at_utc)}</span>
          <span>报告仅含指标与哈希，不含正文、凭据或原始游标</span>
        </div>
      )}

      {blockers.length > 0 && !active && (
        <details className="connector-acceptance-blockers" open>
          <summary>查看阻断项（{blockers.length}）</summary>
          <div>
            {blockers.map((check) => (
              <article key={check.check_id}>
                <strong>{checkLabel(check.check_id)}</strong>
                <span>{check.detail || '实际结果未达到验收门槛。'}</span>
              </article>
            ))}
          </div>
        </details>
      )}

      {latest?.acceptance_ready && !active && (
        <div className="connector-acceptance-success">
          当前连接已满足 Pilot 真实租户准入门槛，可进入试点运行。
        </div>
      )}
    </section>
  );
}

export default ConnectorAcceptancePanel;
