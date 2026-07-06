import { useCallback } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { usePipelineData } from '../api/data';
import { useToast } from '../components/useToast';
import { buildReportData, renderReportHTML } from '../api/report';
import { formatDurationMs } from '../lib/display';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import type { Finding } from '../types';

type JsonRecord = Record<string, unknown>;

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

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function asNum(v: unknown, fallback = 0): number {
  const parsed = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(parsed) ? parsed : fallback;
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

function getSeverityWeight(severity: Finding['severity']) {
  if (severity === 'P0') return 3;
  if (severity === 'P1') return 2;
  return 1;
}

function getFindingModule(finding: Finding) {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '核心业务').trim() || '核心业务';
}

function campaignStatusLabel(status: string): string {
  if (status === 'blocked') return 'Campaign 已阻塞';
  if (status === 'coverage_deferred') return '覆盖已递延';
  if (status === 'completed') return 'Campaign 已完成';
  if (status === 'active') return 'Campaign 进行中';
  return 'Campaign 状态待同步';
}

function campaignDetail(status: string, deferredReason: string, nextCampaignReason: string): string {
  if (status === 'blocked') return `当前扫描未进入执行阶段：${deferredReason || '缺少经过批准的来源、范围、环境或测试数据条件'}。${nextCampaignReason ? ` 下一步：${nextCampaignReason}。` : ''}`;
  if (status === 'coverage_deferred') return `自动范围已到边界，剩余覆盖被明确递延，而非判定为已覆盖。${deferredReason ? ` 原因：${deferredReason}。` : ''}${nextCampaignReason ? ` 下一步：${nextCampaignReason}。` : ''}`;
  if (status === 'completed') return '本 Campaign 的可执行范围已按真实回执收口；后续范围变化会创建新的 Campaign。';
  if (status === 'active') return '当前 Campaign 仍在执行或补证；只有完整真实回执可以形成 confirmed 结果。';
  return '当前没有可用的 Campaign 治理状态。';
}

function getExecutiveHeadline(defectCount: number, p0Count: number, highPriorityCount: number, clueCount: number, campaignStatus: string, deferredReason: string) {
  if (campaignStatus === 'blocked') return `本轮 Campaign 已阻塞：${deferredReason || '缺少进入执行的必要合同'}。`;
  if (campaignStatus === 'coverage_deferred') return `本轮覆盖已递延：${deferredReason || '自动执行边界已到达'}。`;
  if (defectCount > 0 && p0Count > 0) return `已确认 ${defectCount} 个可交付缺陷，其中 ${p0Count} 个会直接影响发布。`;
  if (defectCount > 0) return `已确认 ${defectCount} 个可交付缺陷，可直接进入整改与验收闭环。`;
  if (clueCount > 0) return `本轮尚未形成可交付缺陷，内部仍有 ${clueCount} 条线索正在补证。`;
  if (highPriorityCount === 0) return '当前未发现可交付缺陷，但仍需结合 Campaign 覆盖状态判断发布结论。';
  return '当前没有形成客户可交付缺陷，建议继续进行真实场景扫描。';
}

function getExecutiveDescription(defectCount: number, clueCount: number, evidenceScore: number, modulesCount: number, campaignStatus: string, deferredReason: string, nextCampaignReason: string) {
  if (campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred') return campaignDetail(campaignStatus, deferredReason, nextCampaignReason);
  if (defectCount > 0) return `本页只展示已验证、可复现、具备原始证据的缺陷结果。当前已覆盖 ${modulesCount} 个业务模块，证据可信度 ${evidenceScore}%。`;
  if (clueCount > 0) return '当前没有站得住的客户缺陷，说明系统仍处于补证阶段。本轮内部线索不会进入客户交付，待形成真实证据后再升级展示。';
  return '当前结果代表本轮没有形成客户可交付缺陷。后续新增扫描结果后，这里会自动更新业务结论。';
}

export function Dashboard() {
  usePageTitle('风险总览');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { data, loading, error, refetch } = usePipelineData(project);
  const { navigateToProjectPath } = useProjectNavigation();
  const toast = useToast();

  const handleExport = useCallback(async () => {
    if (!data) return;
    try {
      const record = asRecord(data);
      toast.show('正在生成评级报告...', 'info');
      const findings = ((record.defects || record.risks || []) as Finding[]);
      const valueMetrics = asRecord(record.value_metrics);
      const scores = asRecord(valueMetrics.scores);
      const reportData = buildReportData({
        projectName: asText(record.project_name) || project,
        industry: asText(record.industry),
        totalBugs: findings.length,
        beiScore: asNum(scores.bei),
        bdsScore: String(scores.bds || '0.0'),
        bcsScore: asNum(scores.bcs),
        runtimeProbes: asNum(asRecord(record.business_flow_summary).total),
        dbConfirmed: asNum(asRecord(record.db_verification).confirmed),
        findings,
        dbFindings: [],
      });
      const html = renderReportHTML(reportData);
      const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
      toast.show('评级报告已在新标签页打开', 'success');
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '导出失败';
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
          <div className="bei-card dashboard-loading-card"><div className="dashboard-loading-ring" /><Skeleton h={16} w={120} /><div className="dashboard-loading-gap-sm"><Skeleton h={12} w={180} /></div></div>
          <div className="bei-details">
            {[1, 2].map((item) => (
              <div key={item} className="mini-card dashboard-loading-mini-card"><Skeleton h={44} w={44} br={10} /><div className="dashboard-loading-flex"><Skeleton h={16} w="60%" /><div className="dashboard-loading-gap-xs"><Skeleton h={12} w="80%" /></div></div></div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error && !data) return <StatePanel eyebrow="连接状态" title="后端暂时不可用" description={error} action={<button className="btn btn-primary" onClick={refetch}>重新连接</button>} />;
  if (!project) return <StatePanel eyebrow="客户选择" title="请先选择客户项目" description="风险总览只展示真实项目数据。选择客户后，界面会按该项目的检测结果与证据链自动刷新。" />;

  const record = asRecord(data);
  const findings = ((record.defects || record.risks || []) as Finding[]);
  const clues = ((record.clues || []) as Finding[]);
  const valueMetrics = asRecord(record.value_metrics);
  const scanMeta = asRecord(record.scan_meta);
  const campaign = asRecord(record.campaign);
  const campaignStatus = asText(campaign.campaign_status).toLowerCase();
  const campaignDeferredReason = asText(campaign.coverage_deferred_reason);
  const nextCampaignReason = asText(campaign.next_campaign_reason);
  const campaignScope = asText(campaign.scope_id);
  const campaignEnvironment = asText(campaign.environment_ref);
  const campaignAttempted = asNum(campaign.attempted_slice_count);
  const campaignConfirmed = asNum(campaign.confirmed_slice_count);
  const coverageGaps = Array.isArray(record.coverage_gaps) ? record.coverage_gaps.length : 0;
  const governanceNeedsAction = campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred';

  const totalRiskCount = findings.length;
  const p0Count = findings.filter((finding) => finding.severity === 'P0').length;
  const p1Count = findings.filter((finding) => finding.severity === 'P1').length;
  const clueCount = clues.length;
  const evidenceTrust = asNum(valueMetrics.evidence_trust_score, 0);
  const highPriorityCount = p0Count + p1Count;
  const modules = Array.from(new Set(findings.map(getFindingModule).filter(Boolean)));
  const modulesCount = modules.length;
  const validatedDefects = findings.filter((finding) => finding.evidence_quality?.level === 'validated').length;
  const deliveryReadiness = findings.length > 0 ? Math.round((validatedDefects / findings.length) * 100) : 0;
  const hasMaterializedMetrics = totalRiskCount > 0 || clueCount > 0 || asNum(asRecord(record.business_flow_summary).total, 0) > 0 || Boolean(campaignStatus) || coverageGaps > 0;
  const topFindings = [...findings].sort((a, b) => {
    const severityGap = getSeverityWeight(b.severity) - getSeverityWeight(a.severity);
    return severityGap !== 0 ? severityGap : (b.evidence_quality?.score || 0) - (a.evidence_quality?.score || 0);
  }).slice(0, 3);
  const topFamilyLabel = findings[0]?.defect_family_label || findings[0]?.defect_family || '核心业务';
  const executiveHeadline = getExecutiveHeadline(totalRiskCount, p0Count, highPriorityCount, clueCount, campaignStatus, campaignDeferredReason);
  const executiveDescription = getExecutiveDescription(totalRiskCount, clueCount, evidenceTrust, modulesCount, campaignStatus, campaignDeferredReason, nextCampaignReason);
  const conclusion = campaignStatus === 'blocked' ? 'Campaign 已阻塞' : campaignStatus === 'coverage_deferred' ? '覆盖已递延' : p0Count > 0 ? '存在阻断发布缺陷' : totalRiskCount > 0 ? '建议进入整改验收' : '当前无可交付缺陷';
  const conclusionDetail = campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred'
    ? campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason)
    : p0Count > 0 ? `${p0Count} 个 P0 缺陷需要优先闭环。` : totalRiskCount > 0 ? `${highPriorityCount} 个高风险问题建议先处理。` : '本轮结果需结合 Campaign 覆盖状态作为当前阶段风险结论。';

  const secondarySummarySection = (
    <section className="customer-secondary-grid">
      <article className="customer-secondary-card">
        <span className="customer-value-kicker">本轮交付说明</span>
        <div className="customer-secondary-meta">
          <span><em>最近扫描</em><b>{formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}</b></span>
          <span><em>本轮耗时</em><b>{formatDurationMs(asNum(scanMeta.total_ms))}</b></span>
          <span><em>结果评级</em><b>{asText(scanMeta.grade) || riskRatingLabel(evidenceTrust)}</b></span>
        </div>
      </article>
      {campaignStatus && (
        <article className={`customer-secondary-card${governanceNeedsAction ? ' muted' : ''}`}>
          <span className="customer-value-kicker">Campaign 治理</span>
          <h3>{campaignStatusLabel(campaignStatus)}</h3>
          <p>{campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason)}</p>
          <div className="customer-secondary-meta">
            <span><em>范围</em><b>{campaignScope || '待登记'}</b></span>
            <span><em>环境</em><b>{campaignEnvironment || '待登记'}</b></span>
            <span><em>切片</em><b>{campaignConfirmed}/{campaignAttempted || 0} 已确认</b></span>
            <span><em>覆盖缺口</em><b>{coverageGaps}</b></span>
          </div>
        </article>
      )}
      {clueCount > 0 && (
        <article className="customer-secondary-card muted">
          <span className="customer-value-kicker">内部待跟进</span>
          <h3>{clueCount} 条线索仍在补证</h3>
          <p>这部分只供内部运营使用，不进入客户缺陷交付，避免把待补证线索误展示成已确认问题。</p>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/clues', project)}>进入内部线索页</button>
        </article>
      )}
    </section>
  );

  if (!hasMaterializedMetrics) {
    return (
      <div>
        <div className="page-header"><div><h1>{asText(record.project_name) || project} · 行为风险总览</h1><p>当前项目还没有形成真实风险数据或验证结果。</p></div></div>
        <StatePanel eyebrow="结果状态" title="当前还没有形成可展示的真实指标" description="本项目暂未产生行为发现、执行探针或数据验证结果。运行检测后，页面会自动切换为真实业务视图。" />
      </div>
    );
  }

  return (
    <div>
      <section className="customer-showcase mb-4">
        <div className="customer-showcase-main">
          <span className="panel-kicker">客户成果</span>
          <h1>{asText(record.project_name) || project} · {executiveHeadline}</h1>
          <p>{executiveDescription}</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">可交付缺陷 {totalRiskCount}</span>
            <span className="summary-pill">阻断发布 {p0Count}</span>
            <span className="summary-pill">Campaign {campaignStatusLabel(campaignStatus)}</span>
            <span className="summary-pill">覆盖缺口 {coverageGaps}</span>
          </div>
          <div className="customer-showcase-actions">
            <button className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project)}>查看客户缺陷</button>
            <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button>
            <button className="btn btn-secondary" onClick={handleExport}>导出成果摘要</button>
          </div>
        </div>
        <div className="customer-showcase-side">
          <div className={`customer-status-card ${campaignStatus === 'blocked' || p0Count > 0 ? 'danger' : campaignStatus === 'coverage_deferred' || totalRiskCount > 0 ? 'warning' : 'success'}`}>
            <span>当前结论</span>
            <strong>{conclusion}</strong>
            <p>{conclusionDetail}</p>
          </div>
          <div className="customer-status-meta">
            <span><em>最近扫描</em><b>{formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}</b></span>
            <span><em>证据达标</em><b>{deliveryReadiness}%</b></span>
            <span><em>本轮说明</em><b>{asNum(scanMeta.run_count) ? `第 ${asNum(scanMeta.run_count)} 轮` : campaignStatus ? campaignStatusLabel(campaignStatus) : '首次结果'}</b></span>
          </div>
        </div>
      </section>

      <div className="customer-summary-grid mb-4">
        {[
          { label: '客户可交付', val: totalRiskCount, tone: 'primary', note: totalRiskCount > 0 ? '已验证、可复现、可验收' : governanceNeedsAction ? '未把未覆盖范围误判为无风险' : '当前没有 confirmed 缺陷' },
          { label: '阻断发布', val: p0Count, tone: 'danger', note: p0Count > 0 ? '需要立即闭环' : governanceNeedsAction ? '需先补齐 Campaign 合同或范围' : '当前无阻断项' },
          { label: 'Campaign 切片', val: `${campaignConfirmed}/${campaignAttempted}`, tone: 'warning', note: campaignStatus ? campaignStatusLabel(campaignStatus) : '尚未登记 Campaign' },
          { label: '覆盖缺口', val: coverageGaps, tone: 'neutral', note: coverageGaps > 0 ? '需在后续 Campaign 补齐' : '当前未报告范围缺口' },
        ].map((item) => (
          <article key={item.label} className={`customer-summary-card tone-${item.tone}`}><span>{item.label}</span><strong>{item.val}</strong><small>{item.note}</small></article>
        ))}
      </div>

      <section className="customer-value-grid mb-4">
        <article className="customer-value-card"><span className="customer-value-kicker">发布建议</span><h2>{campaignStatus === 'blocked' ? '暂不形成发布结论，先补齐执行合同' : campaignStatus === 'coverage_deferred' ? '先评审递延范围，再决定是否扩大 Campaign' : p0Count > 0 ? '建议暂停发布，先处理阻断缺陷' : totalRiskCount > 0 ? '建议带着缺陷清单推进整改验收' : '当前没有可交付缺陷，可继续观察后续轮次'}</h2><p>{campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred' ? campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason) : p0Count > 0 ? '当前存在会直接影响业务履约或发布安全的高风险缺陷。' : totalRiskCount > 0 ? '当前结果已经足以形成客户整改清单，不需要再从线索里筛。' : '本轮输出可以作为当前阶段的风险结论，但建议继续保持持续检测。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">客户价值</span><h2>{totalRiskCount > 0 ? `本轮交付 ${totalRiskCount} 个已验证缺陷` : governanceNeedsAction ? '本轮价值在于明确暴露可测试性边界' : '本轮价值在于给出明确风险结论'}</h2><p>{totalRiskCount > 0 ? `缺陷集中在 ${topFamilyLabel} 等方向，证据可信度 ${evidenceTrust}% ，可直接进入修复与复验。` : governanceNeedsAction ? '系统没有把缺少来源、环境、测试数据或递延覆盖伪装成“通过”，可直接用于推动补齐条件。' : '当前没有把未补证线索冒充成客户缺陷，避免误导客户对结果质量的判断。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">交付边界</span><h2>{coverageGaps > 0 ? `当前有 ${coverageGaps} 项覆盖缺口` : clueCount > 0 ? `内部仍有 ${clueCount} 条待补证线索` : '当前没有待补证线索'}</h2><p>{coverageGaps > 0 ? '覆盖缺口不会进入客户缺陷数量，需要通过新的资料、环境、数据或授权条件在后续 Campaign 中关闭。' : clueCount > 0 ? '这些线索不会进入客户成果展示，只作为内部继续采证与复验的运营池。' : '当前结果已经清晰收口，没有把内部线索混进客户视图。'}</p></article>
      </section>

      {topFindings.length === 0 && <div className="mb-4">{secondarySummarySection}</div>}

      <section className="customer-focus-section mb-4">
        <div className="customer-section-head"><div><span className="panel-kicker">重点缺陷</span><h2>客户应该优先关注的结果</h2></div><button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看完整缺陷清单</button></div>
        {topFindings.length === 0 ? (
          <section className="findings-empty-state compact"><span className="findings-empty-kicker">当前结论</span><h3>{governanceNeedsAction ? campaignStatusLabel(campaignStatus) : '当前没有客户可交付缺陷'}</h3><p>{governanceNeedsAction ? campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason) : clueCount > 0 ? `本轮仅有 ${clueCount} 条内部线索仍在补证，客户侧暂不展示。` : '当前没有 confirmed 缺陷，说明本轮结果未发现可交付问题。'}</p></section>
        ) : (
          <div className="customer-focus-list">
            {topFindings.map((finding) => (
              <article key={finding.id} className="customer-focus-card"><div className="customer-focus-head"><span className={`severity ${finding.severity.toLowerCase()}`}>{finding.severity}</span><strong>{finding.title}</strong></div><p>{finding.business_summary || finding.business_impact?.summary || finding.actual || '该问题已形成可交付缺陷结论。'}</p><div className="customer-focus-meta"><span><em>影响模块</em><b>{getFindingModule(finding)}</b></span><span><em>证据状态</em><b>{finding.evidence_quality?.label || '已归档'}</b></span><span><em>复现稳定性</em><b>{finding.proof?.repro_rate ?? 0}%</b></span></div></article>
            ))}
          </div>
        )}
      </section>

      {topFindings.length > 0 && secondarySummarySection}
    </div>
  );
}

export default Dashboard;
