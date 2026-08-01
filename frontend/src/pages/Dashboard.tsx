import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { emitScanCompleted, isCustomerReadyFinding, usePipelineData } from '../api/data';
import { runRegression } from '../api/client';
import { useToast } from '../components/useToast';
import { buildReportData, renderReportHTML } from '../api/report';
import { formatDurationMs } from '../lib/display';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { TechnicalDiagnostics } from '../components/TechnicalDiagnostics';
import { Skeleton, StatePanel } from '../components/dashboard/DashboardPrimitives';
import { ValueHero } from '../components/dashboard/ValueHero';
import { ScanFacts } from '../components/dashboard/ScanFacts';
import { DecisionCards } from '../components/dashboard/DecisionCards';
import { EnterpriseUnderstandingPanel } from '../components/dashboard/EnterpriseUnderstandingPanel';
import { JourneyStrip } from '../components/dashboard/JourneyStrip';
import { TrustPanel, type TrustSignal } from '../components/dashboard/TrustPanel';
import { DiscoveryFunnelPanel } from '../components/dashboard/DiscoveryFunnelPanel';
import {
  asRecord, asText, asNum, formatScanTime,
  getSeverityWeight, getFindingModule, riskLevel, releaseDecision,
  getExecutiveHeadline, campaignStatusLabel, campaignDetail,
} from '../lib/dashboard-utils';
import type { Finding } from '../types';

export function Dashboard() {
  usePageTitle('价值总览');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { data, loading, error, refetch } = usePipelineData(project);
  const { navigateToProjectPath } = useProjectNavigation();
  const toast = useToast();
  const [regressionRunningMode, setRegressionRunningMode] = useState('');

  const handleExport = useCallback(async () => {
    if (!data) return;
    try {
      const record = asRecord(data);
      toast.show('正在生成价值报告...', 'info');
      const findings = ((record.defects || record.risks || []) as Finding[]);
      const valueMetrics = asRecord(record.value_metrics);
      const scores = asRecord(valueMetrics.scores);
      const modulesCovered = new Set(findings.map(getFindingModule).filter(Boolean)).size;
      const reportData = buildReportData({ projectName: asText(record.project_name) || project, industry: asText(record.industry), totalBugs: findings.length, beiScore: asNum(scores.bei), bdsScore: String(scores.bds || '0.0'), bcsScore: asNum(scores.bcs), runtimeProbes: asNum(asRecord(record.business_flow_summary).total), dbConfirmed: asNum(asRecord(record.db_verification).confirmed), findings, dbFindings: [], modulesCovered });
      const html = renderReportHTML(reportData);
      const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
      window.open(URL.createObjectURL(blob), '_blank');
      toast.show('价值报告已在新标签页打开', 'success');
    } catch (caught: unknown) { toast.show(`导出失败: ${caught instanceof Error ? caught.message : '未知错误'}`, 'danger'); }
  }, [project, data, toast]);

  const handleRegressionRun = useCallback(async (mode: 'smoke' | 'release') => {
    if (!project) return;
    setRegressionRunningMode(mode);
    try {
      toast.show(`正在执行 ${mode === 'smoke' ? 'Smoke' : 'Release'} 回归...`, 'info');
      const result = await runRegression(project, { mode });
      emitScanCompleted(project);
      await refetch();
      const gs = asText(result.ci_feedback?.gate_status) || 'unknown';
      const fc = asNum(result.summary?.failed_count);
      toast.show(`回归完成：${gs}${fc > 0 ? `，失败 ${fc} 项` : ''}`, gs === 'failed' ? 'danger' : gs === 'passed' ? 'success' : 'warning');
    } catch (caught: unknown) { toast.show(caught instanceof Error ? caught.message : '回归执行失败', 'danger'); }
    finally { setRegressionRunningMode(''); }
  }, [project, refetch, toast]);

  if (loading) return (
    <div>
      <div className="value-hero">
        <Skeleton h={16} w={120} br={8} />
        <div style={{ marginTop: 16 }}><Skeleton h={32} w="60%" br={6} /></div>
        <div style={{ marginTop: 8 }}><Skeleton h={16} w="80%" br={4} /></div>
        <div className="value-hero-metrics" style={{ marginTop: 24 }}>
          {[1, 2, 3, 4].map((i) => <div key={i} className="value-hero-metric"><Skeleton h={36} w={60} br={6} /><Skeleton h={14} w={80} br={4} /></div>)}
        </div>
      </div>
    </div>
  );
  if (error && !data) return <StatePanel eyebrow="连接状态" title="后端暂时不可用" description={error} action={<button className="btn btn-primary" onClick={refetch}>重新连接</button>} />;
  if (!project) {
    return (
      <div>
        <StatePanel eyebrow="开始使用" title="选择客户项目，查看检测结论" description="QualiBug 是企业系统的独立行为验证层：用真实执行证据，把软件风险变成可复现、可验收的业务结论。请先在右上角选择客户工作区，或按下面四步完成首次接入。" />
        <JourneyStrip onNavigate={(path) => navigateToProjectPath(path, '')} />
      </div>
    );
  }

  const record = asRecord(data);
  const findings = ((record.defects || record.risks || []) as Finding[]).filter(isCustomerReadyFinding);
  const clues = ((record.clues || []) as Finding[]);
  const valueMetrics = asRecord(record.value_metrics);
  const scanMeta = asRecord(record.scan_meta);
  const formalCounts = asRecord(record.formal_count_projection);
  const knowledgeSummary = asRecord(record.knowledge_summary);

  const totalRiskCount = findings.length;
  const p0Count = findings.filter((f) => f.severity === 'P0').length;
  const p1Count = findings.filter((f) => f.severity === 'P1').length;
  const currentScanDefects = asNum(formalCounts.formal_customer_deliverable_count, totalRiskCount);
  const currentScanP0Count = Math.min(p0Count, currentScanDefects);
  const modules = Array.from(new Set(findings.map(getFindingModule).filter(Boolean)));
  const modulesCount = modules.length;
  const evidenceTrust = asNum(valueMetrics.evidence_trust_score, 0);
  const aiTestPoints = asNum(valueMetrics.ai_equivalent_test_points, asNum(asRecord(record.business_flow_summary).total, 0));
  const clueCount = clues.length;
  const evidencePackCount = findings.filter((f) => (f.evidence_chain?.length || 0) > 0).length;

  const campaign = asRecord(record.campaign);
  const campaignStatus = asText(campaign.campaign_status).toLowerCase();
  const campaignDeferredReason = asText(campaign.coverage_deferred_reason);
  const pipelineHealth = asRecord(record.pipeline_health);
  const pipelineHealthStatus = asText(pipelineHealth.status) || asText(scanMeta.pipeline_health_status);
  const pipelineFailedSafe = pipelineHealthStatus === 'FAILED_SAFE';
  const pipelineBlocked = pipelineHealthStatus === 'BLOCKED';
  const pipelineUnhealthy = pipelineFailedSafe || pipelineBlocked;

  const executiveHeadline = getExecutiveHeadline(currentScanDefects, currentScanDefects, currentScanP0Count, clueCount, campaignStatus, campaignDeferredReason);
  const conclusion = pipelineFailedSafe ? '检测异常（非"无问题"）' : pipelineBlocked ? '检测执行被阻断' : campaignStatus === 'blocked' ? '检测暂停' : campaignStatus === 'coverage_deferred' ? '部分范围待后续检测' : currentScanP0Count > 0 ? `发现 ${currentScanDefects} 个已确认缺陷，拦截 ${currentScanP0Count} 个 P0 阻断发布` : currentScanDefects > 0 ? '建议进入整改验收' : '当前未发现阻断性问题';
  const level = riskLevel(conclusion);
  const decision = releaseDecision(currentScanP0Count, currentScanDefects, pipelineUnhealthy, campaignStatus === 'blocked');

  const hasMaterializedMetrics = totalRiskCount > 0 || clueCount > 0 || asNum(asRecord(record.business_flow_summary).total, 0) > 0 || Boolean(campaignStatus) || Object.keys(asRecord(record.discovery_funnel)).length > 0;

  // 「为什么可信」信号：全部来自后端真实字段；缺失字段如实标注「后端暂未提供」。
  const deliveryGuard = asRecord(record.customer_delivery_guard);
  const hasDeliveryGuard = Object.keys(deliveryGuard).length > 0;
  const guardDeliverable = deliveryGuard.customer_deliverable === true && deliveryGuard.safe_for_customer === true;
  const guardBlockReasons = (Array.isArray(deliveryGuard.block_reasons) ? deliveryGuard.block_reasons : []).map(asText).filter(Boolean);
  const trustSignals: TrustSignal[] = [
    {
      key: 'campaign',
      title: '检测治理',
      tone: !campaignStatus ? 'neutral' : campaignStatus === 'blocked' ? 'danger' : campaignStatus === 'coverage_deferred' ? 'warning' : 'success',
      statusLabel: campaignStatus ? campaignStatusLabel(campaignStatus) : '未上报',
      description: campaignStatus ? campaignDetail(campaignStatus, campaignDeferredReason, '') : '本轮尚未上报检测治理状态。',
      unreported: !campaignStatus,
    },
    {
      key: 'pipeline',
      title: '检测链路健康',
      tone: !pipelineHealthStatus ? 'neutral' : pipelineUnhealthy ? 'danger' : 'success',
      statusLabel: !pipelineHealthStatus ? '未上报' : pipelineFailedSafe ? '异常（空结果 ≠ 无问题）' : pipelineBlocked ? '执行被阻断' : '健康',
      description: !pipelineHealthStatus ? '本轮尚未上报检测链路健康状态。' : pipelineUnhealthy ? (asText(pipelineHealth.operator_note) || '检测链路存在问题，本轮结论不能当作"无问题"。') : '检测链路完整执行，异常会如实暴露而不是静默吞掉。',
      unreported: !pipelineHealthStatus,
    },
    {
      key: 'delivery-guard',
      title: '交付守卫',
      tone: !hasDeliveryGuard ? 'neutral' : guardDeliverable ? 'success' : 'warning',
      statusLabel: !hasDeliveryGuard ? '后端暂未提供' : guardDeliverable ? '交付已放行' : '交付未放行',
      description: !hasDeliveryGuard
        ? '交付守卫判定（customer_delivery_guard）尚未随本轮结果上报。'
        : guardDeliverable
          ? '每个进入交付的问题都通过了正式交付门禁校验。'
          : guardBlockReasons.length > 0 ? `阻塞原因：${guardBlockReasons.join('、')}` : '门禁通过不等于交付放行，需守卫明确确认。',
      unreported: !hasDeliveryGuard,
    },
    {
      key: 'cleanup',
      title: '受控写入与清理回执',
      tone: 'neutral',
      statusLabel: '后端暂未提供',
      description: '所有写入均经受控沙箱执行并生成清理回执；每轮的写入 / 清理汇总尚未随结果上报，接入后在此展示。',
      unreported: true,
    },
    {
      key: 'evidence-trust',
      title: '结论可靠度',
      tone: evidenceTrust > 0 ? 'success' : 'neutral',
      statusLabel: evidenceTrust > 0 ? `${evidenceTrust}%` : '待评估',
      description: evidenceTrust > 0 ? '后端基于证据完整性计算的可靠度评分，可用于上线评审与验收。' : '本轮尚未形成可评分的证据集合，评分保持"待评估"而不是虚构数字。',
      unreported: evidenceTrust <= 0,
    },
  ];
  const topFindings = [...findings].sort((a, b) => { const sg = getSeverityWeight(b.severity) - getSeverityWeight(a.severity); return sg !== 0 ? sg : (b.evidence_quality?.score || 0) - (a.evidence_quality?.score || 0); }).slice(0, 3);

  if (!hasMaterializedMetrics) {
    return (
      <div>
        <div className="page-header"><div><h1>{asText(record.project_name) || project} · 价值总览</h1><p>当前项目还没有形成真实检测数据。企业理解进度与执行准备状态如下。</p></div></div>
        <EnterpriseUnderstandingPanel
          summary={knowledgeSummary}
          onOpenMaterials={() => navigateToProjectPath('/settings', project)}
        />
        <JourneyStrip onNavigate={(path) => navigateToProjectPath(path, project)} />
        <section className="empty-value-promise">
          <h2>运行首次检测后，您将看到：</h2>
          <div className="empty-value-grid">
            <div className="empty-value-card"><strong>已确认问题清单</strong><span>每个问题都有原始证据和复现路径</span></div>
            <div className="empty-value-card"><strong>发布安全建议</strong><span>基于真实检测结果的发布决策参考</span></div>
            <div className="empty-value-card"><strong>本轮检测事实</strong><span>真实执行的验证点、耗时与触达模块，不做估算</span></div>
            <div className="empty-value-card"><strong>覆盖与缺口分析</strong><span>覆盖缺口如实展示——空结果不等于系统没有问题</span></div>
          </div>
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/campaigns', project)}>启动首次检测</button>
        </section>
      </div>
    );
  }

  return (
    <div className="customer-results-page">
      {/* 英雄区 */}
      <ValueHero
        projectName={asText(record.project_name) || project}
        conclusion={conclusion}
        headline={executiveHeadline}
        level={level}
        decision={decision}
        metrics={{ confirmedDefects: currentScanDefects, p0Count: currentScanP0Count, testPoints: aiTestPoints, modulesCount }}
        scanTime={formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}
        evidenceTrust={evidenceTrust}
      />

      <EnterpriseUnderstandingPanel
        summary={knowledgeSummary}
        onOpenMaterials={() => navigateToProjectPath('/settings', project)}
      />

      {/* 检测事实区 — 只展示真实执行与后端记账数字 */}
      <ScanFacts
        testPoints={aiTestPoints}
        durationMs={asNum(scanMeta.total_ms)}
        modulesCount={modulesCount}
        evidenceTrust={evidenceTrust}
      />

      <DiscoveryFunnelPanel funnel={record.discovery_funnel} />

      {/* 决策卡片 */}
      <DecisionCards cards={[
        { role: 'CTO / 技术VP', title: '发布决策', value: decision.label, detail: decision.advice },
        { role: '测试 / 质量负责人', title: '证据与验收', value: evidencePackCount > 0 ? `${evidencePackCount} 个证据包` : '待生成', detail: evidencePackCount > 0 ? '每个已确认问题都附原始请求、响应与复现路径，可直接进入整改验收' : '形成已确认问题后，这里会出现可回放、可验收的证据包' },
        { role: '项目经理', title: '风险拦截', value: `${currentScanP0Count} 个 P0`, detail: currentScanP0Count > 0 ? '阻断性问题已在发布前暴露，需优先安排修复' : `当前无阻断性问题${p1Count > 0 ? `，另有 ${p1Count} 个 P1 待评估` : ''}` },
      ]} />

      {/* 行动区 */}
      <div className="action-bar">
        <span className="action-bar-title">下一步该做什么</span>
        <button className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project)}>查看问题清单</button>
        <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据</button>
        <button className="btn btn-secondary" onClick={handleExport}>导出报告</button>
        <button className="btn btn-secondary" onClick={() => void handleRegressionRun('release')} disabled={regressionRunningMode !== ''}>
          {regressionRunningMode === 'release' ? '回归中...' : '执行回归'}
        </button>
      </div>

      {/* 重点关注 Top 3 */}
      <section className="focus-section">
        <div className="focus-section-head">
          <h2>重点关注</h2>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看完整清单</button>
        </div>
        {topFindings.length === 0 ? (
          <div className="focus-list">
            <div className="focus-card">
              <p>{clueCount > 0 ? `本轮仅有 ${clueCount} 条内部线索仍在补证，当前无已确认问题。` : '当前没有需要优先处理的问题。'}</p>
            </div>
          </div>
        ) : (
          <div className="focus-list">
            {topFindings.map((f) => (
              <article key={f.id} className={`focus-card severity-${f.severity.toLowerCase()}`}>
                <div className="focus-card-head">
                  <span className={`severity-badge ${f.severity.toLowerCase()}`}>{f.severity}</span>
                  <strong>{f.title}</strong>
                </div>
                <p>{f.business_summary || f.business_impact?.summary || f.actual || '该问题已形成确认结论。'}</p>
                <div className="focus-card-meta">
                  <span>模块 <b>{getFindingModule(f)}</b></span>
                  <span>证据 <b>{f.evidence_quality?.label || '未评分'}</b></span>
                  <span>复现 <b>{f.proof?.repro_rate != null ? `${f.proof.repro_rate}%` : '未上报'}</b></span>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {/* 为什么可信 — 治理 / 健康 / 守卫 / 回执 / 可靠度 */}
      <TrustPanel signals={trustSignals} />

      {/* 技术诊断 — 默认折叠 */}
      <TechnicalDiagnostics>
        <section className="customer-secondary-grid">
          <article className="customer-secondary-card">
            <span className="customer-value-kicker">本轮检测说明</span>
            <div className="customer-secondary-meta">
              <span><em>最近扫描</em><b>{formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}</b></span>
              <span><em>本轮耗时</em><b>{formatDurationMs(asNum(scanMeta.total_ms))}</b></span>
              <span><em>等效测试点</em><b>{aiTestPoints}</b></span>
              <span><em>结论可靠度</em><b>{evidenceTrust > 0 ? `${evidenceTrust}%` : '待评估'}</b></span>
            </div>
          </article>
          {campaignStatus && (
            <article className="customer-secondary-card">
              <span className="customer-value-kicker">检测治理</span>
              <h3>{campaignStatusLabel(campaignStatus)}</h3>
              <p>{campaignDetail(campaignStatus, campaignDeferredReason, '')}</p>
            </article>
          )}
          {pipelineUnhealthy && (
            <article className="customer-secondary-card">
              <span className="customer-value-kicker">检测链路健康</span>
              <h3>{pipelineFailedSafe ? '检测异常：空结果 ≠ 无问题' : '执行被阻断'}</h3>
              <p>{asText(pipelineHealth.operator_note) || '检测链路存在问题。'}</p>
            </article>
          )}
          {clueCount > 0 && (
            <article className="customer-secondary-card">
              <span className="customer-value-kicker">内部待跟进</span>
              <h3>{clueCount} 条线索仍在补证</h3>
              <p>只供内部运营使用，不进入客户问题交付。</p>
            </article>
          )}
        </section>
      </TechnicalDiagnostics>
    </div>
  );
}

export default Dashboard;
