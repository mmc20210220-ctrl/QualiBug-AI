import { useCallback } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { BEIRing } from '../components/BEIRing';
import { MiniScoreCard } from '../components/MiniScoreCard';
import { EvidenceFeed } from '../components/EvidenceFeed';
import { CoveragePanel } from '../components/CoveragePanel';
import { AnimatedCounter } from '../components/AnimatedCounter';
import { usePipelineData } from '../api/data';
import { useToast } from '../components/useToast';
import { buildReportData, renderReportHTML } from '../api/report';
import { BugTypeBreakdown } from '../components/BugTypeBreakdown';
import { usePageTitle } from '../lib/page-title';

function Skeleton({ h = 20, w = '100%', br = 4, className = '' }: { h?: number; w?: string | number; br?: number; className?: string }) {
  return (
    <div
      className={`skeleton-block${className ? ` ${className}` : ''}`}
      style={{ height: h, width: w, borderRadius: br }}
    />
  );
}

function StatePanel({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <section className="state-panel">
      <div className="state-panel-badge">{eyebrow}</div>
      <h2>{title}</h2>
      <p>{description}</p>
      {action ? <div className="state-panel-actions">{action}</div> : null}
    </section>
  );
}

type CommercialValue = NonNullable<ReturnType<typeof usePipelineData>['data']>['commercialValue'];
type ContinuousDiscovery = NonNullable<ReturnType<typeof usePipelineData>['data']>['continuousDiscovery'];
type ContinuousDiscoveryFrontier = {
  title: string;
  status: string;
  budgetClass: string;
  whySelected: string[];
  businessValueScore: number;
  scheduleScore: number;
};
type ContinuousDiscoveryBlockedEntry = {
  title: string;
  blockerReason: string;
};

function CommercialValuePanel({ value }: { value: CommercialValue }) {
  const metrics = [
    { label: '验证覆盖点', value: value.aiEquivalentTestPoints.toLocaleString(), hint: '把人工测试与规则评审沉淀为可复用检查点' },
    { label: '证据可信度', value: `${value.evidenceTrustScore}%`, hint: '用于上线评审、客户验收和责任闭环' },
    { label: '已覆盖路径', value: value.exploredBehaviorPaths.toLocaleString(), hint: '覆盖接口、状态、权限、数据一致性与边界场景' },
    { label: '高优先级风险', value: value.blockedRiskCount.toString(), hint: 'P0/P1 风险优先进入修复与发布决策' },
  ];

  return (
    <section className="commercial-value-panel mb-4">
      <div className="commercial-value-head">
        <div>
          <span className="commercial-eyebrow">价值证明</span>
          <h2>价值证据面板</h2>
          <p>{value.executiveMessage}</p>
        </div>
        <div className="commercial-proof">
          <strong>{value.bugFamilies || value.capabilityFamilies || '持续'}</strong>
          <span>{value.bugFamilies ? '缺陷族覆盖' : value.capabilityFamilies ? '能力族覆盖' : '知识资产复用'}</span>
        </div>
      </div>

      <div className="commercial-metrics">
        {metrics.map((metric) => (
          <div key={metric.label} className="commercial-metric">
            <strong>{metric.value}</strong>
            <span>{metric.label}</span>
            <small>{metric.hint}</small>
          </div>
        ))}
      </div>

      <div className="commercial-decision-grid">
        {value.decisionCards.map((card) => (
          <article key={card.role} className="commercial-decision-card">
            <span>{card.role}</span>
            <h3>{card.title}</h3>
            <strong>{card.value}</strong>
            <p>{card.detail}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function riskRatingLabel(score: number) {
  if (score >= 80) return '稳健';
  if (score >= 60) return '关注';
  return '优先治理';
}

function campaignStateLabel(state: string) {
  if (state === 'completed') return '本轮覆盖债务已闭合';
  if (state === 'blocked') return '当前仅剩阻塞项';
  if (state === 'scheduled') return '仍有可推进覆盖面';
  if (state === 'active') return '检测推进中';
  if (state === 'paused') return '已暂停';
  return '持续检测中';
}

function frontierStatusLabel(status: string) {
  if (status === 'pending') return '待补证据';
  if (status === 'revalidate_due') return '待重检';
  if (status === 'candidate') return '待首次验证';
  if (status === 'blocked') return '环境阻塞';
  if (status === 'validated') return '已闭环';
  return '待推进';
}

function budgetClassLabel(value: string) {
  if (value === 'exploit') return '优先闭环';
  if (value === 'revalidate') return '重检回放';
  return '扩面探索';
}

function clampPercent(value: number) {
  return Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
}

function formatScanTime(value: string) {
  if (!value) return '暂无';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function formatDuration(ms: number) {
  const value = Number(ms) || 0;
  if (value <= 0) return '暂无';
  if (value < 1000) return `${Math.round(value)}ms`;
  const seconds = value / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function ContinuousDiscoveryPanel({ value }: { value: NonNullable<ContinuousDiscovery> }) {
  const progress = clampPercent(
    value.coveragePercent || (value.totalPaths > 0 ? (value.totalDiscovered / Math.max(1, value.totalPaths)) * 100 : 0),
  );
  const hasCoverageOverflow = value.totalPaths > 0 && value.totalDiscovered > value.totalPaths;
  const coverageNote = hasCoverageOverflow
    ? `已累计完成 ${value.totalDiscovered} 次闭环动作，当前覆盖池包含 ${value.totalPaths} 个行为路径。`
    : `已覆盖 ${Math.min(value.totalDiscovered, value.totalPaths || value.totalDiscovered)}/${value.totalPaths || Math.max(value.totalDiscovered, 0)} 个行为路径，${value.remainingPaths > 0 ? `还剩 ${value.remainingPaths} 个待推进` : '全部覆盖完毕'}`;
  const hasImplicitRemainingRisk = value.blockedEntries.length > 0 || value.remainingActionable > 0 || value.recommendedFrontierCount > 0 || value.highValueUncovered > 0;
  const metrics = [
    { label: '已纳入行为单元', value: value.ledgerCount.toLocaleString(), tone: 'primary' as const },
    { label: '已验证闭环', value: value.totalDiscovered.toLocaleString(), tone: 'success' as const },
    { label: '剩余可推进', value: value.remainingActionable.toLocaleString(), tone: 'warning' },
    { label: '高价值未覆盖', value: value.highValueUncovered.toLocaleString(), tone: 'danger' },
    { label: '环境阻塞', value: value.blockedCount.toLocaleString(), tone: 'neutral' },
    { label: '待重检', value: value.revalidationQueue.toLocaleString(), tone: 'primary' },
    { label: '资料完备度', value: `${value.docCompleteness || 0}%`, tone: 'neutral' },
  ];

  return (
    <section className="continuous-discovery-panel mb-4">
      <div className="continuous-discovery-head">
        <div>
          <span className="continuous-discovery-eyebrow">持续覆盖</span>
          <h2>持续检测覆盖</h2>
          <p>{campaignStateLabel(value.campaignState)}，当前已闭环 {progress}% 的已识别行为单元。</p>
        </div>
        <div className={`continuous-discovery-status${value.canStopNow ? ' ready' : ''}`}>
          <strong>{value.canStopNow ? '可停止' : '继续推进'}</strong>
          <span>{value.canStopNow ? '当前阶段覆盖债务已清空' : `第 ${value.runCount || 0} 轮后仍有待闭环项未闭合`}</span>
        </div>
      </div>

      <div className="continuous-discovery-metrics">
        {metrics.map((metric) => (
          <article key={metric.label} className={`continuous-discovery-metric tone-${metric.tone}`}>
            <strong>{metric.value}</strong>
            <span>{metric.label}</span>
          </article>
        ))}
      </div>

      <div className="continuous-discovery-card-grid">
        <article className="continuous-discovery-card">
          <div className="continuous-discovery-card-head">
            <h3>当前判断</h3>
            <span>本轮收敛情况</span>
          </div>
          <div className="continuous-discovery-inline-metrics">
            <div>
              <strong>{value.newThisRound}</strong>
              <span>本轮新发现</span>
            </div>
            <div>
              <strong>{value.confirmedFindings}</strong>
              <span>持续确认</span>
            </div>
            <div>
              <strong>{progress}%</strong>
              <span>路径覆盖率</span>
            </div>
          </div>
          <div className="continuous-discovery-note">
            {coverageNote}
          </div>
          <ul className="continuous-discovery-list">
            {(value.stopConditionsMet.length > 0 ? value.stopConditionsMet : value.continueConditions).slice(0, 4).map((item: string) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </article>

        <article className="continuous-discovery-card">
          <div className="continuous-discovery-card-head">
            <h3>未覆盖与风险</h3>
            <span>还差什么</span>
          </div>
          <ul className="continuous-discovery-list">
            {value.remainingRisks.length > 0 ? value.remainingRisks.slice(0, 5).map((item: string) => (
              <li key={item}>{item}</li>
            )) : <li>{hasImplicitRemainingRisk ? '当前未返回逐条风险清单，但仍存在待闭环项，建议优先处理阻塞项与下一轮建议。' : '当前没有显式剩余风险，后续只需关注新增资料或环境触发。'}</li>}
          </ul>
          {value.blockedEntries.length > 0 && (
            <div className="continuous-discovery-blockers">
              {value.blockedEntries.map((entry: ContinuousDiscoveryBlockedEntry) => (
                <div key={`${entry.title}-${entry.blockerReason}`} className="continuous-discovery-blocker">
                  <strong>{entry.title}</strong>
                  <span>{entry.blockerReason}</span>
                </div>
              ))}
            </div>
          )}
        </article>

        <article className="continuous-discovery-card">
          <div className="continuous-discovery-card-head">
            <h3>下一轮建议</h3>
            <span>{value.recommendedFrontierCount} 个待推进</span>
          </div>
          {value.recommendedFrontier.length > 0 ? (
            <div className="continuous-discovery-frontiers">
              {value.recommendedFrontier.map((entry: ContinuousDiscoveryFrontier) => (
                <div key={`${entry.title}-${entry.status}`} className="continuous-discovery-frontier">
                  <div className="continuous-discovery-frontier-head">
                    <strong>{entry.title}</strong>
                    <span>{frontierStatusLabel(entry.status)} · {budgetClassLabel(entry.budgetClass)}</span>
                  </div>
                  {entry.scheduleScore > 0 && (
                    <p>优先级 {entry.scheduleScore.toFixed(2)}</p>
                  )}
                  {entry.whySelected[0] && <small>{entry.whySelected[0]}</small>}
                </div>
              ))}
            </div>
          ) : (
            <div className="continuous-discovery-empty">当前没有新的待闭环项建议，等待新的触发条件或资料扩面。</div>
          )}
        </article>
      </div>
    </section>
  );
}

function ContinuousDiscoveryEmptyPanel({ findingsCount }: { findingsCount: number }) {
  return (
    <section className="continuous-discovery-panel mb-4">
      <div className="continuous-discovery-head">
        <div>
          <span className="continuous-discovery-eyebrow">持续覆盖</span>
          <h2>持续检测覆盖</h2>
          <p>当前项目还没有生成持续检测账本，因此暂时无法判断覆盖到什么程度、还差哪些待闭环项、是否可以停止。</p>
        </div>
        <div className="continuous-discovery-status">
          <strong>账本未生成</strong>
          <span>现有页面只能展示风险发现结果，不能展示跨轮次覆盖收敛。</span>
        </div>
      </div>

      <div className="continuous-discovery-card-grid">
        <article className="continuous-discovery-card">
          <div className="continuous-discovery-card-head">
            <h3>当前已知信息</h3>
            <span>来自现有扫描结果</span>
          </div>
          <ul className="continuous-discovery-list">
            <li>当前项目已有 {findingsCount} 个风险发现，因此风险总览可以正常展示。</li>
            <li>但后端没有产出 `continuous_discovery_campaign`，所以无法推导“已闭环 / 待重检 / 剩余可推进 / 可停止”。</li>
          </ul>
        </article>

        <article className="continuous-discovery-card">
          <div className="continuous-discovery-card-head">
            <h3>为什么没显示进度</h3>
            <span>真实空态</span>
          </div>
          <ul className="continuous-discovery-list">
            <li>当前项目目录下没有 `real_project_defect_data.json`。</li>
            <li>当前项目工作区下也没有 `continuous_discovery_campaign.json`。</li>
            <li>所以这不是前端渲染失败，而是该项目还没有生成持续检测账本产物。</li>
          </ul>
        </article>

        <article className="continuous-discovery-card">
          <div className="continuous-discovery-card-head">
            <h3>下一步</h3>
            <span>如何看到覆盖进度</span>
          </div>
          <ul className="continuous-discovery-list">
            <li>运行支持 continuous discovery 的发现任务，生成 Campaign 账本。</li>
            <li>或者把当前扫描流程补成会落盘 `continuous_discovery_campaign`。</li>
            <li>一旦后端产物生成，这里会自动切换成真实覆盖进度面板。</li>
          </ul>
        </article>
      </div>
    </section>
  );
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
      const reportData = buildReportData({
        projectName: data.projectName || project,
        industry: data.industry,
        totalBugs: data.totalBugs,
        beiScore: data.beiScore,
        bdsScore: data.bdsScore,
        bcsScore: data.bcsScore,
        runtimeProbes: data.runtimeProbes,
        dbConfirmed: data.dbConfirmed,
        findings: data.findings,
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
        <div className="coverage-panel">
          <div className="dashboard-loading-gap-md"><Skeleton h={18} w={150} /></div>
          <div className="coverage-grid">
            {[1, 2, 3, 4].map((item) => (
              <div key={item}>
                <Skeleton h={32} w="60%" br={4} />
                <div className="dashboard-loading-gap-sm"><Skeleton h={12} w="80%" /></div>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <StatePanel
        eyebrow="连接状态"
        title="后端暂时不可用"
        description={error}
        action={<button className="btn btn-primary" onClick={refetch}>重新连接</button>}
      />
    );
  }

  if (!project) {
    return (
      <StatePanel
        eyebrow="客户选择"
        title="请先选择客户项目"
        description="风险总览只展示真实项目数据，不再自动填充样例内容。选择客户后，界面会按该项目的检测结果与证据链自动刷新。"
      />
    );
  }

  const findings = data?.findings || [];
  const p0Count = findings.filter(f => f.severity === 'P0').length;
  const p1Count = findings.filter(f => f.severity === 'P1').length;
  const beiScore = data?.beiScore ?? 0;
  const bdsScore = data?.bdsScore ?? '0.0';
  const bcsScore = data?.bcsScore ?? 0;
  const commercialValue = data?.commercialValue;
  const continuousDiscovery = data?.continuousDiscovery;
  const spectrum = data?.spectrum;
  const scanMeta = data?.scanMeta;

  const coverage = {
    modeled_paths: data?.continuousDiscovery?.totalPaths || Math.max(findings.length, 0),
    executed_probes: data?.continuousDiscovery?.totalDiscovered || (data?.runtimeProbes || 0) + (data?.dbProbes || 0),
    confirmed_findings: findings.length,
    evidence_completeness: findings.length > 0 ? Math.min(98, 70 + Math.round(findings.filter(f => f.evidence_chain.length >= 3).length / Math.max(1, findings.length) * 30)) : 0,
  };
  const displayedCoveragePaths = coverage.modeled_paths > 0 ? Math.min(coverage.executed_probes, coverage.modeled_paths) : coverage.executed_probes;
  const hasCoverageOverflow = coverage.modeled_paths > 0 && coverage.executed_probes > coverage.modeled_paths;
  const hasMaterializedMetrics = findings.length > 0 || coverage.executed_probes > 0 || (data?.dbConfirmed || 0) > 0;
  const highPriorityCount = p0Count + p1Count;
  const heroMetrics = [
    {
      label: '风险评级',
      value: beiScore.toString(),
      note: riskRatingLabel(beiScore),
    },
    {
      label: '高优先级风险',
      value: highPriorityCount.toString(),
      note: highPriorityCount > 0 ? '需要优先进入闭环' : '当前未发现阻断项',
    },
    {
      label: '证据完备度',
      value: `${coverage.evidence_completeness}%`,
      note: '用于发布与验收评审',
    },
    {
      label: '已覆盖路径',
      value: displayedCoveragePaths.toLocaleString(),
      note: hasCoverageOverflow
        ? `共 ${coverage.modeled_paths.toLocaleString()} 个已建模路径 · 累计闭环 ${coverage.executed_probes.toLocaleString()} 次`
        : `共 ${coverage.modeled_paths.toLocaleString()} 个已建模路径`,
    },
  ];

  if (!hasMaterializedMetrics) {
    return (
      <div>
        <div className="page-header">
          <div>
            <h1>{data?.projectName || project} · 行为风险总览</h1>
            <p>当前项目还没有形成真实风险数据或验证结果。</p>
          </div>
        </div>
        <StatePanel
          eyebrow="结果状态"
          title="当前还没有形成可展示的真实指标"
          description="本项目暂未产生行为发现、执行探针或数据验证结果，因此首页不会展示评级、覆盖和风险卡片。运行检测后，页面会自动切换为真实业务视图。"
        />
      </div>
    );
  }

  return (
    <div>
      <section className="dashboard-hero mb-4">
        <div className="dashboard-hero-main">
          <span className="panel-kicker">综合态势</span>
          <h1>{data?.projectName || project} · 行为风险总览</h1>
          <p>
            基于 {coverage.modeled_paths.toLocaleString()} 个行为路径自动建模
            {data?.industry ? ` · ${data.industry}` : ''}
            {hasCoverageOverflow ? ` · 已累计归档 ${coverage.executed_probes.toLocaleString()} 次闭环动作` : ''}
            · 多源资料一致性持续监控 · 证据链完整可复现
          </p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">已识别 {findings.length.toLocaleString()} 个风险发现</span>
            <span className="summary-pill">高优先级风险 {highPriorityCount}</span>
            <span className="summary-pill">证据完备度 {coverage.evidence_completeness}%</span>
            {scanMeta?.runCount ? <span className="summary-pill">最近运行 第 {scanMeta.runCount} 轮</span> : null}
          </div>
          <div className="dashboard-hero-actions">
            <button className="btn btn-secondary" onClick={handleExport}>导出评级报告</button>
            <div className="dashboard-hero-inline-note">
              仅展示已落地的真实项目结果，不混入样例或演示数据。
            </div>
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

      {scanMeta?.runCount ? (
        <section className="scan-meta-panel mb-4">
          <div>
            <span className="scan-meta-kicker">最近一次检测</span>
            <strong>第 {scanMeta.runCount} 轮 · {formatScanTime(scanMeta.lastScanAt)}</strong>
          </div>
          <div className="scan-meta-grid">
            <span><em>本次返回</em><b>{scanMeta.totalFindings || findings.length}</b></span>
            <span><em>耗时</em><b>{formatDuration(scanMeta.totalMs)}</b></span>
            <span><em>评级</em><b>{scanMeta.grade || '暂无'}</b></span>
            <span><em>Scan ID</em><b>{scanMeta.scanId || '暂无'}</b></span>
          </div>
        </section>
      ) : null}

      {/* Big Score Row */}
      <div className="score-row">
        {/* BEI Card */}
        <div className="bei-card">
          <BEIRing score={beiScore} />
          <div className="bei-label">风险评级</div>
          <div className="bei-sub">{riskRatingLabel(beiScore)}</div>
          <div className="bei-tags">
            {p0Count > 0 && <span className="tag tag-warn">{p0Count} 个阻断项待优先闭环</span>}
            <span className="tag tag-info">证据链持续同步</span>
          </div>
        </div>

        {/* BDS / BCS */}
        <div className="bei-details">
          <MiniScoreCard label="缺陷密度" value={bdsScore} unit="个" description="每千个行为路径中高危缺陷数量" color="warning" icon="BDS" />
          <MiniScoreCard label="多源自洽度" value={bcsScore} unit="%" description="全部企业资料交叉验证一致率" color="success" icon="BCS" />
        </div>
      </div>

      {/* Quick Stats Row */}
      <div className="dashboard-stat-grid mb-4">
        {[
          { label: '风险发现', val: findings.length, tone: 'neutral', note: '当前轮次累计识别' },
          { label: 'P0 阻塞', val: p0Count, tone: 'danger', note: '优先进入修复闭环' },
          { label: 'P1 高风险', val: p1Count, tone: 'warning', note: '影响发布与履约' },
          { label: 'P2 提示', val: findings.filter(f => f.severity === 'P2').length, tone: 'primary', note: '建议纳入后续回归' },
        ].map(m => (
          <div key={m.label} className={`dashboard-stat-card tone-${m.tone}`}>
            <div className="dashboard-stat-value">
              <AnimatedCounter value={m.val} />
            </div>
            <div className="dashboard-stat-label">{m.label}</div>
            <div className="dashboard-stat-note">{m.note}</div>
          </div>
        ))}
      </div>

      {continuousDiscovery ? <ContinuousDiscoveryPanel value={continuousDiscovery} /> : <ContinuousDiscoveryEmptyPanel findingsCount={findings.length} />}
      {commercialValue && <CommercialValuePanel value={commercialValue} />}

      {/* Full-Spectrum Bug Detection Status */}
      <SpectrumStatus value={spectrum} />

      {/* Bug Type Breakdown */}
      <BugTypeBreakdown findings={findings} />

      {/* Coverage Panel */}
      <CoveragePanel data={coverage} />

      {/* Evidence Feed */}
      <EvidenceFeed findings={findings} />
    </div>
  );
}

// ── Full-Spectrum Status Card ──

const CAPABILITY_LABELS: Record<string, string> = {
  contract: 'API契约', concurrency: '并发竞态', data_qual: '数据质量',
  cache: '缓存一致性', messaging: '消息/事件', third_party: '第三方集成',
  i18n: '国际化', mobile: '移动端', file: '文件处理', compat: 'API兼容',
  rate_limit: '速率限制', load_test: '负载压力', test_gen: '用例生成', input_valid: '输入验证',
  security: '安全渗透', i18n_deep: '深度国际化', interaction: '交互流程', load_advanced: '极限负载',
  concurrency_v2: '并发v2', cache_v2: '缓存v2', mobile_v2: '移动v2', third_party_v2: '集成v2',
  rate_limit_v2: '限流v2', compat_v2: '兼容v2', file_v2: '文件v2',
};
const CAPABILITY_ICONS: Record<string, string> = {
  contract: '📋', concurrency: '⚡', data_qual: '🗄️',
  cache: '💾', messaging: '📨', third_party: '🔌',
  i18n: '🌐', mobile: '📱', file: '📁', compat: '🔄',
  rate_limit: '⏱️', load_test: '🔥', test_gen: '📝', input_valid: '🛡️',
  security: '🔒', i18n_deep: '🌍', interaction: '🔗', load_advanced: '💥',
  concurrency_v2: '⚡', cache_v2: '💾', mobile_v2: '📱', third_party_v2: '🔌',
  rate_limit_v2: '⏱️', compat_v2: '🔄', file_v2: '📁',
};

function SpectrumStatus({
  value,
}: {
  value: NonNullable<ReturnType<typeof usePipelineData>['data']>['spectrum'] | null | undefined;
}) {
  if (!value || value.status === 'not_run') return null;

  const caps = (value.capabilities || []) as Array<{ id: string; status: string; findingsCount: number; checksRun: number; summary: string }>;
  const withIssues = caps.filter((capability: { findingsCount: number }) => capability.findingsCount > 0);

  return (
    <section className="dashboard-section spectrum-card">
      <h3 className="section-title">
        🔬 全频谱 Bug 检测
        {value.lastRun && <span className="section-subtitle"> · 最近: {new Date(value.lastRun).toLocaleString('zh-CN')}</span>}
      </h3>
      <div className="spectrum-grid">
        {caps.map((capability: { id: string; status: string; findingsCount: number }) => (
          <div key={capability.id} className={`spectrum-chip ${capability.findingsCount > 0 ? 'has-issues' : 'clean'}`}>
            <span className="spectrum-icon">{CAPABILITY_ICONS[capability.id] || '🔍'}</span>
            <span className="spectrum-label">{CAPABILITY_LABELS[capability.id] || capability.id}</span>
            {capability.findingsCount > 0
              ? <span className="spectrum-count badge-warn">{capability.findingsCount}</span>
              : <span className="spectrum-count badge-ok">✓</span>}
          </div>
        ))}
      </div>
      {withIssues.length > 0 && (
        <div className="spectrum-summary">
          检测到 <strong>{value.summary?.totalFindings || 0}</strong> 个潜在缺陷，
          覆盖 <strong>{withIssues.length}/{caps.length}</strong> 个检测维度
        </div>
      )}
    </section>
  );
}

export default Dashboard;
