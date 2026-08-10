import { useEffect, useMemo, useState } from 'react';
import {
  getLiveScanStatus,
  type LiveScanStatus,
  type ScanStageProgressItem,
} from '../../api/live-scan-status';
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

const LIVE_STAGE_DEFINITIONS = [
  ['enterprise_understanding', '企业资料理解'],
  ['scenario_planning', '场景与义务生成'],
  ['test_data_assessment', '测试数据准备 / 就绪核验'],
  ['runtime_execution', '真实探针执行'],
  ['evidence_collection', '结果观察与证据收集'],
  ['delivery_finalization', '交付门禁与报告'],
] as const;

function liveStageLabel(item?: ScanStageProgressItem): string {
  if (!item) return '等待服务端阶段上报';
  const detail = item.detail ? ` · ${item.detail}` : '';
  switch (item.status) {
    case 'active': return `进行中${detail}`;
    case 'completed': return `已完成${detail}`;
    case 'failed': return `失败${detail}`;
    case 'blocked': return `已阻断${detail}`;
    case 'unreported': return '服务端未实时上报';
    default: return '尚未进入 / 尚未实时上报';
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
      const stageMap = liveStatus?.scan_stage_progress?.stages || {};
      return LIVE_STAGE_DEFINITIONS.map(([key, label]) => [label, liveStageLabel(stageMap[key])]);
    }
    if (detail.phase === 'failed') {
      return [
        ['企业资料理解', '未确认'],
        ['场景与义务生成', '未确认'],
        ['测试数据准备 / 就绪核验', '未确认'],
        ['真实探针执行', '未确认'],
        ['结果观察与证据收集', '未确认'],
        ['交付门禁与报告', '未确认'],
      ];
    }
    return [
      ['企业资料理解', '本轮规划主链已返回'],
      ['场景与义务生成', detail.campaignStatus || '本轮计划已返回'],
      ['测试数据准备 / 就绪核验', detail.testDataStatus || '未报告'],
      ['真实探针执行', detail.executionStatus || '未报告'],
      ['结果观察与证据收集', `${detail.evidenceCount} 条真实请求证据`],
      ['交付门禁与报告', detail.grade ? `${detail.grade} · 覆盖 ${detail.coverage}` : `覆盖 ${detail.coverage}`],
    ];
  }, [detail, liveStatus?.scan_stage_progress?.stages]);

  if (!detail) return null;

  const tone = lifecycleTone(detail);
  const serverConfirmed = detail.phase === 'submitted' && liveStatus?.active_scan_live === true;
  const realStageReported = Boolean(liveStatus?.scan_stage_progress);
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
              <span className="muted">阶段遥测</span>
              <p>{realStageReported ? '已收到主链真实阶段回执' : '等待主链阶段回执'}</p>
            </div>
          </div>
          <p className="settings-hint mt-3">
            六个阶段都来自服务端真实执行边界：企业理解与场景规划来自正式规划主链，真实探针来自 experiment runner，证据状态来自实际证据持久化，测试数据来自合同/收据核验，交付阶段来自真实发布门禁并持续到最终报告与结果收口。发布门禁已经给出 fail/blocked 结论时，阶段仍可能继续显示“进行中”，因为报告尚在生成；只有最终结果收口后才显示完成。任何阶段都不会按计时器或百分比推测。
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
