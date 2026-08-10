import { useEffect, useMemo, useState } from 'react';
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

export function RunLifecycleBanner() {
  const [detail, setDetail] = useState<RunLifecycleDetail | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const handleLifecycle = (event: Event) => {
      const next = (event as CustomEvent<RunLifecycleDetail>).detail;
      if (!next) return;
      setDetail(next);
      setNow(Date.now());
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
  const title = detail.phase === 'submitted'
    ? '后台正在执行真实验证'
    : detail.phase === 'completed'
      ? '运行回执已返回'
      : '运行请求未完成';
  const statusText = detail.phase === 'submitted'
    ? '请求已提交，等待服务端完成'
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
        <p className="settings-hint">
          当前扫描接口是同步请求，服务端尚未暴露分阶段实时进度。这里不会用计时器伪造阶段推进；下面各阶段只在最终回执返回后确认。
        </p>
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
