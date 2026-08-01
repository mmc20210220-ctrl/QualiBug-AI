import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getConnectorAcceptanceReport,
  listConnectorAcceptanceReports,
  runConnectorAcceptance,
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
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!projectId || !connectorId) return;
    setLoading(true);
    setError('');
    try {
      const inventory = await listConnectorAcceptanceReports(projectId, connectorId);
      const nextLatest = inventory.reports[0] || null;
      setLatest(nextLatest);
      if (nextLatest) {
        setReport(await getConnectorAcceptanceReport(projectId, connectorId, nextLatest.report_id));
      } else {
        setReport(null);
      }
    } catch (loadError: unknown) {
      setError(loadError instanceof Error ? loadError.message : '验收状态加载失败。');
    } finally {
      setLoading(false);
    }
  }, [projectId, connectorId]);

  useEffect(() => {
    void load();
  }, [load]);

  const blockers = useMemo(
    () => (report?.checks || []).filter((check) => check.severity === 'BLOCKER' && check.status === 'FAIL'),
    [report],
  );

  const runPilot = async () => {
    setRunning(true);
    setError('');
    try {
      const result = await runConnectorAcceptance(projectId, connectorId, 'pilot');
      setLatest(result);
      setReport(result);
      if (result.acceptance_ready) {
        toast.show('飞书真实租户 Pilot 验收已通过。', 'success');
      } else {
        toast.show(`验收完成，发现 ${result.summary.blocker_failure_count} 个阻断项。`, 'warning');
      }
    } catch (runError: unknown) {
      const message = runError instanceof Error ? runError.message : '验收执行失败。';
      setError(message);
      toast.show(message, 'danger');
    } finally {
      setRunning(false);
    }
  };

  const tone = latest?.acceptance_ready ? 'pass' : latest ? 'fail' : 'idle';
  const status = latest?.acceptance_ready ? '已通过' : latest ? '未通过' : '尚未验收';
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
          disabled={disabled || loading || running}
        >
          {running ? '正在执行两轮验收…' : latest ? '重新运行 Pilot 验收' : '运行 Pilot 验收'}
        </button>
      </div>

      <p className="connector-acceptance-note">
        连续执行两轮只读同步，验证连接、覆盖率、增量复用、检查点和客户资料非修改边界。
        验收固定使用 RETAIN，不删除或修改飞书原资料。
      </p>

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
          <span>验收等级：{latest.profile || 'pilot'}</span>
          <span>最近完成：{formatTime(latest.completed_at_utc)}</span>
          <span>报告仅含指标与哈希，不含正文、凭据或原始游标</span>
        </div>
      )}

      {blockers.length > 0 && (
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

      {latest?.acceptance_ready && (
        <div className="connector-acceptance-success">
          当前连接已满足 Pilot 真实租户准入门槛，可进入试点运行。
        </div>
      )}
    </section>
  );
}

export default ConnectorAcceptancePanel;
