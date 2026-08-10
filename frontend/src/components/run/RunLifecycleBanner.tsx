import { useEffect, useMemo, useState } from 'react';
import { getLiveScanStatus, type LiveScanStatus } from '../../api/live-scan-status';
import { RUN_LIFECYCLE_EVENT, type RunLifecycleDetail } from '../../api/run-center';

function elapsedSeconds(detail: RunLifecycleDetail, now: number): number {
  const end = detail.phase === 'submitted' ? now : detail.finishedAt;
  return Math.max(0, Math.floor((end - detail.startedAt) / 1000));
}

function lifecycleTone(detail: RunLifecycleDetail): 'success' | 'warning' | 'danger' {
  if (detail.phase === 'failed') return 'danger';
  if (detail.phase === 'submitted') return 'warning';

  const executionStatus = detail.executionStatus.toLowerCase();
  const campaignStatus = detail.campaignStatus.toLowerCase();
  const testDataStatus = detail.testDataStatus.toLowerCase();
  if (['blocked', 'failed', 'error'].includes(executionStatus) || campaignStatus === 'blocked') return 'danger';
  if (
    ['plan_only', 'partial', 'partial_coverage', 'coverage_deferred', 'not_executed'].includes(executionStatus)
    || campaignStatus === 'coverage_deferred'
    || (testDataStatus && testDataStatus !== 'ready')
    || detail.totalFindings > 0
  ) return 'warning';
  return 'success';
}

function scanModeLabel(mode?: string): string {
  switch ((mode || '').toLowerCase()) {
    case 'manual_scan': return '标准扫描';
    case 'continuous_scan': return '持续扫描';
    case 'regression':
    case 'regression_scan': return '回归扫描';
    default: return mode || '项目扫描';
  }
}

export function RunLifecycleBanner() {
  const [detail, setDetail] = useState<RunLifecycleDetail | null>(null);
  const [now, setNow] = useState(Date.now());
  const [liveStatus, setLiveStatus] = useState<LiveScanStatus | null>(null);
  const [liveStatusError, setLiveStatusError] = useState('');

  useEffect(() => {
    const handleLifecycle = (event: Event) => {
      const next = (event as CustomEvent<RunLifecycleDetail>).detail;
      if (!next) return;
      setDetail(next);
      setNow(Date.now());
      if (next.phase === 'submitted') {
        setLiveStatus(null);
        setLiveStatusError('');
      }
    };
    window.addEventListener(RUN_LIFECYCLE_EVENT, handleLifecycle);
    return () => window.removeEventListener(RUN_LIFECYCLE_EVENT, handleLifecycle);
  }, []);

  useEffect(() => {
    if (detail?.phase !== 'submitted') return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [detail?.phase]);

  useEffect(() => {
    if (detail?.phase !== 'submitted' || !detail.projectId) return;
    let cancelled = false;

    const refreshLiveStatus = async () => {
      try {
        const status = await getLiveScanStatus(detail.projectId);
        if (cancelled) return;
        setLiveStatus(status);
        setLiveStatusError('');
      } catch (error: unknown) {
        if (cancelled) return;
        setLiveStatusError(error instanceof Error ? error.message : '运行状态读取失败');
      }
    };

    void refreshLiveStatus();
    const timer = window.setInterval(() => void refreshLiveStatus(), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [detail?.phase, detail?.projectId]);

  useEffect(() => {
    if (!detail || detail.phase === 'submitted') return;
    const timer = window.setTimeout(() => setDetail(null), 5000);
    return () => window.clearTimeout(timer);
  }, [detail]);

  const stages = useMemo(() => {
    if (!detail) return [];
    if (detail.phase === 'submitted') {
      return [
        ['企业资料理解', '等待服务端回执'],
        ['场景与义务生成', '等待服务端回执'],
        ['测试数据准备', '等待服务端回执'],
        ['真实探针执行', '等待服务端回执'],
        ['结果观察与证据收集', '等待服务端回执'],
        ['交付门禁与报告', '等待服务端回执'],
      ];
    }
    if (detail.phase === 'failed') {
      return [
        ['企业资料理解', '未确认'],
        ['场景与义务生成', '未确认'],
        ['测试数据准备', '未确认'],
        ['真实探针执行', '未确认'],
        ['结果观察与证据收集', '未确认'],
        ['交付门禁与报告', '未确认'],
      ];
    }
    return [
      ['企业资料理解', '服务端未单独报告'],
      ['场景与义务生成', detail.campaignStatus || '未单独报告'],
      ['测试数据准备', detail.testDataStatus || '未报告'],
      ['真实探针执行', detail.executionStatus || '未报告'],
      ['结果观察与证据收集', `${detail.evidenceCount} 条真实请求证据`],
      ['交付门禁与报告', detail.grade ? `${detail.grade} · 覆盖 ${detail.coverage}` : `覆盖 ${detail.coverage}`],
    ];
  }, [detail]);

  if (!detail) return null;

  const tone = lifecycleTone(detail);
  const serverConfirmed = detail.phase === 'submitted' && liveStatus?.active_scan_live === true;
  const title = detail.phase === 'submitted'
    ? serverConfirmed ? '服务端正在执行真实验证' : '检测请求正在建立运行上下文'
    : detail.phase === 'completed'
      ? '运行回执已返回'
      : '运行请求未完成';
  const statusText = detail.phase === 'submitted'
    ? serverConfirmed
      ? `服务端已确认项目扫描租约 · ${scanModeLabel(liveStatus?.active_scan.mode)}`
      : '请求已提交，等待服务端确认项目扫描租约'
    : detail.phase === 'completed'
      ? `${detail.executionStatus || '已返回'} · ${detail.totalFindings} 条发现`
      : detail.message;

  return (
    <section className={`card mb-4 status-card status-${tone}`} role="status" aria-live="polite">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">真实运行进度</span>
          <h2>{title}</h2>
          <p className="muted">{statusText}</p>
        </div>
        <span className="summary-pill strong">已用时 {elapsedSeconds(detail, now)} 秒</span>
      </div>

      {detail.phase === 'submitted' && (
        <>
          <div className="settings-grid mt-3">
            <div>
              <span className="muted">服务端运行状态</span>
              <p>{serverConfirmed ? '已确认真实扫描正在运行' : '等待服务端登记运行租约'}</p>
            </div>
            <div>
              <span className="muted">服务端已运行</span>
              <p>{serverConfirmed ? `${liveStatus?.active_scan_elapsed_seconds || 0} 秒` : '尚未确认'}</p>
            </div>
            <div>
              <span className="muted">运行模式</span>
              <p>{serverConfirmed ? scanModeLabel(liveStatus?.active_scan.mode) : '等待确认'}</p>
            </div>
            <div>
              <span className="muted">服务端开始时间</span>
              <p>{serverConfirmed ? (liveStatus?.active_scan.started_at_utc || '已启动') : '尚未确认'}</p>
            </div>
          </div>
          <p className="settings-hint mt-3">
            服务端现在提供真实的项目扫描租约状态，但尚未暴露企业资料理解、场景生成、测试数据准备等内部阶段的实时事件；这里不会根据计时器推测内部进度，六个阶段只在真实回执返回后确认。
          </p>
          {liveStatusError && (
            <p className="settings-inline-feedback">
              实时状态暂时不可读取：{liveStatusError}。扫描请求本身仍以最终回执为准。
            </p>
          )}
        </>
      )}

      <div className="settings-grid mt-3">
        {stages.map(([label, value]) => (
          <div key={label}>
            <span className="muted">{label}</span>
            <p>{value}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
