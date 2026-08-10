import { useEffect, useMemo, useState } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { RUN_LIFECYCLE_EVENT, type RunLifecycleDetail } from '../../api/run-center';
import { useProjectNavigation } from '../../lib/project-navigation';

type TerminalRunDetail = Exclude<RunLifecycleDetail, { phase: 'submitted' }>;

type ResultAction = {
  label: string;
  path: string;
};

function executionLabel(status: string): string {
  switch (status.toLowerCase()) {
    case 'executed': return '已真实执行';
    case 'completed': return '已完成真实验证';
    case 'plan_only': return '仅生成计划，未完成执行';
    case 'partial': return '部分执行';
    case 'partial_coverage': return '部分覆盖';
    case 'coverage_deferred': return '覆盖未完成';
    case 'blocked': return '执行被阻断';
    case 'not_executed': return '未执行';
    case 'failed': return '执行失败';
    default: return status || '状态未上报';
  }
}

function isBlocked(detail: TerminalRunDetail): boolean {
  if (detail.phase === 'failed') return true;
  return ['blocked', 'failed', 'error'].includes(detail.executionStatus.toLowerCase())
    || detail.campaignStatus.toLowerCase() === 'blocked';
}

function isIncomplete(detail: TerminalRunDetail): boolean {
  if (detail.phase === 'failed') return false;
  return [
    'plan_only',
    'partial',
    'partial_coverage',
    'coverage_deferred',
    'not_executed',
  ].includes(detail.executionStatus.toLowerCase())
    || detail.campaignStatus.toLowerCase() === 'coverage_deferred'
    || Boolean(detail.testDataStatus && detail.testDataStatus.toLowerCase() !== 'ready');
}

function formatElapsed(detail: TerminalRunDetail): string {
  return `${Math.max(0, Math.round((detail.finishedAt - detail.startedAt) / 1000))} 秒`;
}

export function RunCustomerResultSummary() {
  const location = useLocation();
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const [detail, setDetail] = useState<TerminalRunDetail | null>(null);

  useEffect(() => {
    const handleLifecycle = (event: Event) => {
      const next = (event as CustomEvent<RunLifecycleDetail>).detail;
      if (!next || next.projectId !== project) return;
      if (next.phase === 'submitted') {
        setDetail(null);
        return;
      }
      setDetail(next);
    };
    window.addEventListener(RUN_LIFECYCLE_EVENT, handleLifecycle);
    return () => window.removeEventListener(RUN_LIFECYCLE_EVENT, handleLifecycle);
  }, [project]);

  useEffect(() => {
    if (location.pathname !== '/campaigns') setDetail(null);
  }, [location.pathname]);

  const presentation = useMemo(() => {
    if (!detail) return null;
    const blocked = isBlocked(detail);
    const incomplete = isIncomplete(detail);

    if (detail.phase === 'failed') {
      return {
        tone: 'danger',
        title: '本次验证请求未完成',
        summary: detail.message,
        rangeLabel: '没有取得可用于发布判断的完整回执',
        primary: { label: '检查接入与运行条件', path: '/settings' } satisfies ResultAction,
        secondary: { label: '返回结果总览', path: '/dashboard' } satisfies ResultAction,
      };
    }

    if (blocked) {
      return {
        tone: 'danger',
        title: '本次验证已返回，但执行被阻断',
        summary: '当前结果不能解释为系统安全，也不应直接进入发布结论。先处理阻断条件，再重新验证。',
        rangeLabel: '验证链路存在明确阻断',
        primary: { label: '检查接入与运行条件', path: '/settings' } satisfies ResultAction,
        secondary: { label: '查看结果总览', path: '/dashboard' } satisfies ResultAction,
      };
    }

    if (incomplete) {
      return {
        tone: 'warning',
        title: '本次验证已返回，但覆盖尚未完整',
        summary: detail.totalFindings > 0
          ? `当前运行回执包含 ${detail.totalFindings} 条发现，同时仍有范围未完成；请分别处理已发现问题和未覆盖范围。`
          : '当前运行回执没有发现项，但覆盖尚未完整；不能把 0 条发现直接解释为系统没有问题。',
        rangeLabel: '本轮仍有未完成范围',
        primary: { label: '查看未覆盖范围', path: '/coverage' } satisfies ResultAction,
        secondary: { label: detail.totalFindings > 0 ? '查看问题清单' : '查看结果总览', path: detail.totalFindings > 0 ? '/findings' : '/dashboard' } satisfies ResultAction,
      };
    }

    if (detail.totalFindings > 0) {
      return {
        tone: 'warning',
        title: `本次运行回执包含 ${detail.totalFindings} 条发现`,
        summary: '先进入结果总览确认客户交付口径，再处理已确认问题、证据和回归闭环。运行回执数量本身不替代正式 Finding 交付口径。',
        rangeLabel: '本轮执行已形成终态回执',
        primary: { label: '查看结果总览', path: '/dashboard' } satisfies ResultAction,
        secondary: { label: '查看问题清单', path: '/findings' } satisfies ResultAction,
      };
    }

    return {
      tone: 'success',
      title: '本次验证已完成',
      summary: '当前运行回执没有发现项。请进入结果总览结合覆盖、证据和发布门禁判断，不把运行页的 0 条发现单独解释为系统安全。',
      rangeLabel: '本轮执行已形成终态回执',
      primary: { label: '查看结果总览', path: '/dashboard' } satisfies ResultAction,
      secondary: { label: '查看发布门禁', path: '/release' } satisfies ResultAction,
    };
  }, [detail]);

  if (location.pathname !== '/campaigns' || !project || !detail || !presentation) return null;

  const executionStatus = detail.phase === 'failed' ? '请求失败' : executionLabel(detail.executionStatus);
  const findingsLabel = detail.phase === 'failed' ? '未形成终态数量' : `${detail.totalFindings} 条`;

  return (
    <section className={`card mb-4 status-card status-${presentation.tone}`} aria-label="本次验证客户结果摘要">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">本次验证 · 客户结果摘要</span>
          <h2>{presentation.title}</h2>
          <p className="muted">{presentation.summary}</p>
        </div>
        <span className="summary-pill strong">用时 {formatElapsed(detail)}</span>
      </div>

      <div className="customer-summary-grid mt-3">
        <article className="customer-summary-card">
          <span>执行结果</span>
          <strong>{executionStatus}</strong>
          <small>只展示服务端最终回执，不按前端计时推测执行成功。</small>
        </article>
        <article className="customer-summary-card">
          <span>运行回执发现</span>
          <strong>{findingsLabel}</strong>
          <small>最终客户可交付问题数以价值总览和问题清单的正式口径为准。</small>
        </article>
        <article className="customer-summary-card">
          <span>范围状态</span>
          <strong>{presentation.rangeLabel}</strong>
          <small>覆盖未完成时，0 条发现不能被解释为系统安全。</small>
        </article>
      </div>

      <div className="action-bar mt-3">
        <span className="action-bar-title">下一步</span>
        <button className="btn btn-primary" type="button" onClick={() => navigateToProjectPath(presentation.primary.path, project)}>
          {presentation.primary.label}
        </button>
        <button className="btn btn-secondary" type="button" onClick={() => navigateToProjectPath(presentation.secondary.path, project)}>
          {presentation.secondary.label}
        </button>
      </div>

      <p className="settings-hint mt-3">
        扫描 ID、HAR 请求、Fixture、运行合同和阶段明细继续保留在运行中心原有技术回执中；客户摘要不改变任何后端检测、Finding 或发布门禁结论。
      </p>
    </section>
  );
}
