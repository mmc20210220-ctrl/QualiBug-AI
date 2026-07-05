import { useCallback } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { BEIRing } from '../components/BEIRing';
import { MiniScoreCard } from '../components/MiniScoreCard';
import { EvidenceFeed } from '../components/EvidenceFeed';
import { CoveragePanel } from '../components/CoveragePanel';
import { AnimatedCounter } from '../components/AnimatedCounter';
import { ValueDashboard } from '../components/ValueDashboard';
import { usePipelineData } from '../api/data';
import { useToast } from '../components/useToast';
import { buildReportData, renderReportHTML } from '../api/report';
import { BugTypeBreakdown } from '../components/BugTypeBreakdown';
import { formatDurationMs } from '../lib/display';
import { usePageTitle } from '../lib/page-title';
import type { Finding } from '../types';

function Skeleton({ h = 20, w = '100%', br = 4, className = '' }: { h?: number; w?: string | number; br?: number; className?: string }) {
  return <div className={`skeleton-block${className ? ` ${className}` : ''}`} style={{ height: h, width: w, borderRadius: br }} />;
}

function StatePanel({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return (
    <section className="state-panel">
      <div className="state-panel-badge">{eyebrow}</div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action ? <div className="state-panel-actions">{action}</div> : null}
    </section>
  );
}

function riskRatingLabel(score: number) {
  if (score >= 80) return '稳健';
  if (score >= 60) return '关注';
  return '优先治理';
}

function formatScanTime(value: string) {
  if (!value) return '暂无';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function asNum(v: unknown, fallback = 0): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback;
}

export function Dashboard() {
  usePageTitle('风险总览');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { data, loading, error, refetch } = usePipelineData(project);
  const toast = useToast();

  const handleExport = useCallback(async () => {
    if (!data) return;
    try {
      toast.show('正在生成评级报告...', 'info');
      const findings = (data.risks || []) as Finding[];
      const valueMetrics = data.value_metrics || {};
      const scores = valueMetrics.scores || {};
      const exec = data.executive_summary || {};
      const reportData = buildReportData({
        projectName: data.project_name || project,
        industry: data.industry,
        totalBugs: asNum(exec.total_bugs_found || exec.total_findings, findings.length),
        beiScore: asNum(scores.bei),
        bdsScore: String(scores.bds || '0.0'),
        bcsScore: asNum(scores.bcs),
        runtimeProbes: asNum(data.business_flow_summary?.total),
        dbConfirmed: asNum(data.db_verification?.confirmed),
        findings,
        dbFindings: [],
      });
      const html = renderReportHTML(reportData);
      const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
      toast.show('评级报告已在新标签页打开', 'success');
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : '导出失败';
      toast.show(`导出失败: ${message}`, 'danger');
    }
  }, [project, data, toast]);

  if (loading) {
    return (
      <div>
        <div className="page-header dashboard-loading-header">
          <div className="dashboard-loading-header-main">
            <Skeleton h={28} w="60%" br={6} />
            <div className="dashboard-loading-gap-sm"><Skeleton h={16} w="80%" /></div>
          </div>
          <Skeleton h={36} w={140} br={7} />
        </div>
        <div className="score-row">
          <div className="bei-card dashboard-loading-card">
            <div className="dashboard-loading-ring" />
            <Skeleton h={16} w={120} />
            <div className="dashboard-loading-gap-sm"><Skeleton h={12} w={180} /></div>
          </div>
          <div className="bei-details">
            {[1, 2].map((item) => (
              <div key={item} className="mini-card dashboard-loading-mini-card">
                <Skeleton h={44} w={44} br={10} />
                <div className="dashboard-loading-flex">
                  <Skeleton h={16} w="60%" />
                  <div className="dashboard-loading-gap-xs"><Skeleton h={12} w="80%" /></div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error && !data) {
    return <StatePanel eyebrow="连接状态" title="后端暂时不可用" description={error} action={<button className="btn btn-primary" onClick={refetch}>重新连接</button>} />;
  }

  if (!project) {
    return <StatePanel eyebrow="客户选择" title="请先选择客户项目" description="风险总览只展示真实项目数据。选择客户后，界面会按该项目的检测结果与证据链自动刷新。" />;
  }

  const findings = (data?.risks || []) as Finding[];
  const exec = data?.executive_summary || {};
  const valueMetrics = data?.value_metrics || {};
  const scores = valueMetrics.scores || {};
  const commercialValue = valueMetrics.commercial_value || null;
  const scanMeta = data?.scan_meta || {};

  const totalRiskCount = asNum(exec.total_bugs_found || exec.total_findings, findings.length);
  const p0Count = asNum(exec.critical_bugs, findings.filter(f => f.severity === 'P0').length);
  const p1Count = asNum(exec.high_priority_bugs, findings.filter(f => f.severity === 'P1').length);
  const beiScore = asNum(scores.bei);
  const bdsScore = String(scores.bds || '0.0');
  const bcsScore = asNum(scores.bcs);
  const highPriorityCount = p0Count + p1Count;

  const coverage = {
    modeled_paths: asNum(data?.business_flow_summary?.total, Math.max(totalRiskCount, findings.length)),
    executed_probes: asNum(data?.runtime_verification?.total_probes, 0) + asNum(data?.db_verification?.total, 0),
    confirmed_findings: totalRiskCount,
    evidence_completeness: asNum(scores.evidence_trust_score, findings.length > 0 ? Math.min(98, 70 + Math.round(findings.filter(f => (f.evidence_chain?.length || 0) >= 3).length / Math.max(1, findings.length) * 30)) : 0),
  };

  const hasMaterializedMetrics = totalRiskCount > 0 || findings.length > 0 || coverage.executed_probes > 0;

  if (!hasMaterializedMetrics) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1>{data?.project_name || project} · 行为风险总览</h1>
            <p>当前项目还没有形成真实风险数据或验证结果。</p>
          </div>
        </div>
        <StatePanel eyebrow="结果状态" title="当前还没有形成可展示的真实指标" description="本项目暂未产生行为发现、执行探针或数据验证结果。运行检测后，页面会自动切换为真实业务视图。" />
      </div>
    );
  }

  const heroMetrics = [
    { label: '风险评级', value: beiScore.toString(), note: riskRatingLabel(beiScore) },
    { label: '高优先级风险', value: highPriorityCount.toString(), note: highPriorityCount > 0 ? '需要优先进入闭环' : '当前未发现阻断项' },
    { label: '证据完备度', value: `${coverage.evidence_completeness}%`, note: '用于发布与验收评审' },
    { label: '已覆盖路径', value: coverage.executed_probes.toLocaleString(), note: `共 ${coverage.modeled_paths.toLocaleString()} 个已建模路径` },
  ];

  return (
    <div>
      <section className="dashboard-hero mb-4">
        <div className="dashboard-hero-main">
          <span className="panel-kicker">综合态势</span>
          <h1>{data?.project_name || project} · 行为风险总览</h1>
          <p>
            基于 {coverage.modeled_paths.toLocaleString()} 个行为路径自动建模
            {data?.industry ? ` · ${data.industry}` : ''}
            · 多源资料一致性持续监控 · 证据链完整可复现
          </p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">已识别 {totalRiskCount.toLocaleString()} 个风险发现</span>
            <span className="summary-pill">高优先级风险 {highPriorityCount}</span>
            <span className="summary-pill">证据完备度 {coverage.evidence_completeness}%</span>
            {scanMeta.run_count ? <span className="summary-pill">最近运行 第 {scanMeta.run_count} 轮</span> : null}
          </div>
          <div className="dashboard-hero-actions">
            <button className="btn btn-secondary" onClick={handleExport}>导出评级报告</button>
            <div className="dashboard-hero-inline-note">仅展示已落地的真实项目结果，不混入样例或演示数据。</div>
          </div>
        </div>
        <div className="dashboard-hero-metrics">
          {heroMetrics.map((metric) => (
            <div key={metric.label} className="dashboard-hero-metric">
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
              <small>{metric.note}</small>
            </div>
          ))}
        </div>
      </section>

      {scanMeta.run_count ? (
        <section className="scan-meta-panel mb-4">
          <div>
            <span className="scan-meta-kicker">最近一次检测</span>
            <strong>第 {scanMeta.run_count} 轮 · {formatScanTime(scanMeta.last_scan_at)}</strong>
          </div>
          <div className="scan-meta-grid">
            <span><em>本次返回</em><b>{scanMeta.total_findings || totalRiskCount}</b></span>
            <span><em>耗时</em><b>{formatDurationMs(scanMeta.total_ms)}</b></span>
            <span><em>评级</em><b>{scanMeta.grade || '暂无'}</b></span>
            <span><em>Scan ID</em><b>{scanMeta.scan_id || '暂无'}</b></span>
          </div>
        </section>
      ) : null}

      <div className="score-row">
        <div className="bei-card">
          <BEIRing score={beiScore} />
          <div className="bei-label">风险评级</div>
          <div className="bei-sub">{riskRatingLabel(beiScore)}</div>
          <div className="bei-tags">
            {p0Count > 0 && <span className="tag tag-warn">{p0Count} 个阻断项待优先闭环</span>}
            <span className="tag tag-info">证据链持续同步</span>
          </div>
        </div>
        <div className="bei-details">
          <MiniScoreCard label="缺陷密度" value={bdsScore} unit="个" description="每千个行为路径中高危缺陷数量" color="warning" icon="BDS" />
          <MiniScoreCard label="多源自洽度" value={bcsScore} unit="%" description="全部企业资料交叉验证一致率" color="success" icon="BCS" />
        </div>
      </div>

      <div className="dashboard-stat-grid mb-4">
        {[
          { label: '风险发现', val: totalRiskCount, tone: 'neutral', note: '当前轮次累计识别' },
          { label: 'P0 阻塞', val: p0Count, tone: 'danger', note: '优先进入修复闭环' },
          { label: 'P1 高风险', val: p1Count, tone: 'warning', note: '影响发布与履约' },
          { label: 'P2 提示', val: Math.max(0, totalRiskCount - p0Count - p1Count), tone: 'primary', note: '建议纳入后续回归' },
        ].map(m => (
          <div key={m.label} className={`dashboard-stat-card tone-${m.tone}`}>
            <div className="dashboard-stat-value"><AnimatedCounter value={m.val} /></div>
            <div className="dashboard-stat-label">{m.label}</div>
            <div className="dashboard-stat-note">{m.note}</div>
          </div>
        ))}
      </div>

      <ValueDashboard value={commercialValue} />
      <BugTypeBreakdown findings={findings} />
      <CoveragePanel data={coverage} />
      <EvidenceFeed findings={findings} />
    </div>
  );
}

export default Dashboard;
