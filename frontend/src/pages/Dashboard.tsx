import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { emitScanCompleted, isCustomerReadyFinding, usePipelineData } from '../api/data';
import { runRegression } from '../api/client';
import { useToast } from '../components/useToast';
import { buildReportData, renderReportHTML } from '../api/report';
import { formatDurationMs } from '../lib/display';
import { evidenceDeepLinkSearch } from '../lib/evidence-presentation';
import { deriveFindingVerification, hasFindingReverificationObligation } from '../lib/finding-verification';
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
import { ChainPositioningPanel } from '../components/dashboard/ChainPositioningPanel';
import { MainChainContractPanel } from '../components/dashboard/MainChainContractPanel';
import { RegressionClosurePanel } from '../components/dashboard/RegressionClosurePanel';
import { RegressionGateBanner } from '../components/dashboard/RegressionGateBanner';
import { DashboardFocusFindingCard } from '../components/dashboard/DashboardFocusFindingCard';
import {
  asRecord, asText, asNum, firstNum, formatScanTime,
  getSeverityWeight, getFindingModule, releaseDecision,
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
    const record = asRecord(data);
    const regressionFindings = ((record.defects || record.risks || []) as Finding[]).filter(isCustomerReadyFinding);
    const hasRegressionObligation = regressionFindings.some(hasFindingReverificationObligation);
    if (!hasRegressionObligation) {
      toast.show('当前没有已纳入真实回归套件的验证义务；不会提交空验证请求。', 'warning');
      return;
    }
    setRegressionRunningMode(mode);
    try {
      toast.show(`正在执行 ${mode === 'smoke' ? 'Smoke' : 'Release'} 修复后验证...`, 'info');
      const result = await runRegression(project, { mode });
      emitScanCompleted(project);
      await refetch();
      const gateStatus = asText(result.ci_feedback?.gate_status) || 'unknown';
      const failedCount = asNum(result.summary?.failed_count);
      toast.show(`修复后验证完成：${gateStatus}${failedCount > 0 ? `，仍失败 ${failedCount} 项` : ''}`, gateStatus === 'failed' ? 'danger' : gateStatus === 'passed' ? 'success' : 'warning');
    } catch (caught: unknown) { toast.show(caught instanceof Error ? caught.message : '修复后验证失败', 'danger'); }
    finally { setRegressionRunningMode(''); }
  }, [project, data, refetch, toast]);

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
  const p0Count = findings.filter((finding) => finding.severity === 'P0').length;
  const p1Count = findings.filter((finding) => finding.severity === 'P1').length;
  const regressionEligible = findings.some(hasFindingReverificationObligation);
  const currentScanDefects = asNum(formalCounts.formal_customer_deliverable_count, totalRiskCount);
  const familyShelfDefects = currentScanDefects;
  const currentScanP0Count = Math.min(p0Count, currentScanDefects);
  const currentScanP1Count = Math.min(p1Count, Math.max(0, currentScanDefects - currentScanP0Count));
  const modules = Array.from(new Set(findings.map(getFindingModule).filter(Boolean)));
  const modulesCount = modules.length;
  const evidenceTrust = asNum(valueMetrics.evidence_trust_score, 0);
  const aiTestPoints = asNum(valueMetrics.ai_equivalent_test_points, asNum(asRecord(record.business_flow_summary).total, 0));
  const clueCount = clues.length;
  const evidencePackCount = findings.filter((finding) => (finding.evidence_chain?.length || 0) > 0).length;

  const campaign = asRecord(record.campaign);
  const campaignStatus = asText(campaign.campaign_status).toLowerCase();
  const campaignDeferredReason = asText(campaign.coverage_deferred_reason);
  const continuousCampaign = asRecord(record.continuous_discovery_campaign);
  const campaignSummary = asRecord(continuousCampaign.summary);
  const campaignConfirmed = firstNum(campaignSummary.current_campaign_confirmed_slice_count, campaignSummary.confirmed_slice_count, campaign.confirmed_slice_count);
  const campaignCurrentRawFindings = asNum(campaignSummary.current_campaign_bundle_finding_count_raw);
  const currentScanFindings = asNum(scanMeta.current_report_total_findings, asNum(scanMeta.total_findings, campaignCurrentRawFindings));
  const pipelineHealth = asRecord(record.pipeline_health);
  const pipelineHealthStatus = asText(pipelineHealth.status) || asText(scanMeta.pipeline_health_status);
  const pipelineFailedSafe = pipelineHealthStatus === 'FAILED_SAFE';
  const pipelineBlocked = pipelineHealthStatus === 'BLOCKED';
  const pipelineUnhealthy = pipelineFailedSafe || pipelineBlocked;
  const campaignBlocked = campaignStatus === 'blocked';
  const coverageDeferred = campaignStatus === 'coverage_deferred';
  const resultIncomplete = pipelineUnhealthy || campaignBlocked || coverageDeferred;
  const releaseGate = asRecord(record.release_gate);
  const releaseGateChecks = (Array.isArray(releaseGate.checks) ? releaseGate.checks : []).map((value) => {
    const check = asRecord(value);
    return {
      name: asText(check.name),
      status: asText(check.status),
      detail: asText(check.detail),
    };
  });
  const releaseGateOverall = asText(releaseGate.overall_status || releaseGate.verdict || releaseGate.status);
  const hasReleaseGateData = Object.keys(releaseGate).length > 0;
  const regressionCampaign = Object.keys(asRecord(record.regression_campaign)).length > 0
    ? asRecord(record.regression_campaign)
    : Object.keys(asRecord(record.regression)).length > 0
      ? asRecord(record.regression)
      : asRecord(record.regression_result);
  const regressionGateStatus = asText(asRecord(regressionCampaign.ci_feedback).gate_status).toLowerCase();
  const regressionFailed = regressionGateStatus === 'failed';

  const executiveHeadline = getExecutiveHeadline(currentScanDefects, currentScanDefects, currentScanP0Count, clueCount, campaignStatus, campaignDeferredReason);
  const decision = releaseDecision(
    currentScanP0Count,
    currentScanDefects,
    pipelineUnhealthy,
    campaignBlocked || coverageDeferred,
    releaseGateOverall,
    releaseGateChecks,
    hasReleaseGateData,
    regressionGateStatus,
  );
  const conclusion = pipelineFailedSafe
    ? '检测异常（非"无问题"）'
    : pipelineBlocked
      ? '检测执行被阻断'
      : campaignBlocked
        ? '检测暂停'
        : coverageDeferred
          ? '部分范围待后续检测'
          : currentScanP0Count > 0
            ? `发现 ${currentScanDefects} 个已确认缺陷，拦截 ${currentScanP0Count} 个 P0 阻断发布`
            : regressionFailed
              ? '最新回归门禁失败'
              : currentScanDefects > 0
                ? '建议进入修复后验证'
                : decision.color === 'green'
                  ? '当前未发现阻断性问题'
                  : '当前无已确认阻断问题，发布结论待确认';
  const level = decision.color === 'red' ? 'blocked' : decision.color === 'yellow' ? 'attention' : 'safe';

  const topFindings = [...findings].sort((left, right) => {
    const verificationGap = deriveFindingVerification(right).priority - deriveFindingVerification(left).priority;
    if (verificationGap !== 0) return verificationGap;
    const severityGap = getSeverityWeight(right.severity) - getSeverityWeight(left.severity);
    return severityGap !== 0 ? severityGap : (right.evidence_quality?.score || 0) - (left.evidence_quality?.score || 0);
  }).slice(0, 3);
  const focusFindings = currentScanDefects > 0 ? topFindings : [];
  const highestPriorityFinding = focusFindings[0] || null;

  const nextAction = pipelineUnhealthy
    ? { title: '先恢复检测链路，再判断风险', label: '查看运行状态', path: '/campaigns' }
    : campaignBlocked
      ? { title: '补齐阻断条件后重新检测', label: '处理阻断条件', path: '/settings' }
      : coverageDeferred
        ? { title: '继续覆盖剩余范围', label: '继续检测', path: '/campaigns' }
        : currentScanDefects > 0
          ? { title: '查看已确认问题与验证状态', label: '查看验证', path: '/findings' }
          : regressionFailed
            ? { title: '先查看失败验证，再考虑发布', label: '查看发布门禁', path: '/release' }
            : { title: '确认发布结论', label: '查看发布门禁', path: '/release' };
  const riskInterceptValue = resultIncomplete && currentScanP0Count === 0 ? '结论待确认' : `${currentScanP0Count} 个 P0`;
  const riskInterceptDetail = pipelineUnhealthy
    ? '检测链路异常或被阻断，当前 0 个 P0 不能解释为系统安全。先恢复检测链路再判断发布风险。'
    : campaignBlocked
      ? '本轮检测尚未进入完整执行，当前没有 P0 结论不等于没有阻断风险。'
      : coverageDeferred
        ? '本轮存在明确未覆盖范围，当前 0 个 P0 只代表已覆盖部分，不能直接推导为安全。'
        : currentScanP0Count > 0
          ? '阻断性问题已在发布前暴露，需要在客户修复后由 QualiBug 重新验证。'
          : regressionFailed
            ? '最新回归门禁已失败，已知验证风险不能被“当前无 P0”掩盖。'
            : p1Count > 0
              ? `当前已覆盖范围无 P0，另有 ${p1Count} 个 P1 待验证。`
              : decision.color === 'green'
                ? '项目级 Release Gate 已明确放行；仍应以本轮已上报范围和最新回归状态为边界。'
                : '当前没有已确认 P0，但发布门禁尚未形成明确放行结论。';

  const hasMaterializedMetrics = totalRiskCount > 0 || clueCount > 0 || asNum(asRecord(record.business_flow_summary).total, 0) > 0 || Boolean(campaignStatus) || Object.keys(asRecord(record.discovery_funnel)).length > 0;

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
  const scopeFacts = [
    { label: '本轮可交付', val: currentScanDefects, tone: 'primary', note: currentScanDefects > 0 ? `当前确认 ${currentScanDefects} 条，均通过正式交付门禁` : '当前没有已确认问题' },
    { label: '缺陷货架', val: familyShelfDefects, tone: 'neutral', note: '当前正式交付范围的缺陷口径' },
  ];

  if (!hasMaterializedMetrics) {
    return (
      <div>
        <RegressionGateBanner record={record} />
        <div className="page-header"><div><h1>{asText(record.project_name) || project} · 价值总览</h1><p>当前项目还没有形成真实检测数据。企业理解进度与执行准备状态如下。</p></div></div>
        <EnterpriseUnderstandingPanel
          summary={knowledgeSummary}
          onOpenMaterials={() => navigateToProjectPath('/materials', project)}
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
      <RegressionGateBanner record={record} />

      <ValueHero
        projectName={asText(record.project_name) || project}
        conclusion={conclusion}
        headline={executiveHeadline}
        level={level}
        decision={decision}
        metrics={{
          confirmedDefects: currentScanDefects,
          p0Count: currentScanP0Count,
          p1Count: currentScanP1Count,
          evidencePackCount,
        }}
        scanTime={formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}
        focusFinding={highestPriorityFinding}
        nextAction={{ title: nextAction.title, label: nextAction.label }}
        onNextAction={() => navigateToProjectPath(nextAction.path, project)}
        onOpenFocus={() => {
          if (!highestPriorityFinding) return;
          navigateToProjectPath('/findings', project, evidenceDeepLinkSearch(highestPriorityFinding.id));
        }}
      />

      <EnterpriseUnderstandingPanel
        summary={knowledgeSummary}
        onOpenMaterials={() => navigateToProjectPath('/materials', project)}
      />

      <ScanFacts
        testPoints={aiTestPoints}
        durationMs={asNum(scanMeta.total_ms)}
        modulesCount={modulesCount}
        evidenceTrust={evidenceTrust}
      />

      <DecisionCards cards={[
        { role: 'CTO / 技术VP', title: '发布决策', value: decision.label, detail: decision.advice },
        { role: '测试 / 质量负责人', title: '证据与验收', value: evidencePackCount > 0 ? `${evidencePackCount} 个证据包` : '待生成', detail: evidencePackCount > 0 ? '每个已确认问题都附原始请求、响应与复现路径，可直接用于修复后重新验证' : '形成已确认问题后，这里会出现可回放、可验收的证据包' },
        { role: '项目视角', title: '风险拦截', value: riskInterceptValue, detail: riskInterceptDetail },
      ]} />

      <details className="card mb-4 dashboard-more-actions">
        <summary><strong>更多结果操作</strong> <span className="muted">证据、覆盖、再次检测、报告与修复后验证</span></summary>
        <div className="action-bar mt-3">
          <span className="action-bar-title">当前建议：{nextAction.title}</span>
          {currentScanDefects > 0 && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据</button>}
          {resultIncomplete && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/coverage', project)}>查看未覆盖范围</button>}
          {!resultIncomplete && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/campaigns', project)}>再次检测</button>}
          <button className="btn btn-secondary" onClick={handleExport}>{resultIncomplete ? '导出当前报告' : '导出报告'}</button>
          {currentScanDefects > 0 && (
            <button className="btn btn-secondary" onClick={() => void handleRegressionRun('release')} disabled={regressionRunningMode !== '' || !regressionEligible} title={regressionEligible ? '执行当前已纳入真实回归套件的修复后验证' : '当前没有真实可执行验证义务'}>
              {regressionRunningMode === 'release' ? '正在验证...' : regressionEligible ? '修复后验证' : '暂无可执行验证'}
            </button>
          )}
        </div>
      </details>

      <section className="focus-section">
        <div className="focus-section-head">
          <h2>重点关注</h2>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看完整清单</button>
        </div>
        {focusFindings.length === 0 ? (
          <div className="focus-list">
            <div className="focus-card">
              <p>{clueCount > 0 ? `本轮仅有 ${clueCount} 条内部线索仍在补证，当前无已确认问题。` : resultIncomplete ? '本轮没有已确认问题，但检测尚未完整，不能把空结果解释为系统没有问题。' : decision.color === 'green' ? '本轮已完成范围内没有需要优先验证的问题，项目级 Release Gate 已明确放行。' : '本轮没有已确认问题，但发布门禁尚未形成明确放行结论。'}</p>
            </div>
          </div>
        ) : (
          <div className="focus-list">
            {focusFindings.map((finding) => <DashboardFocusFindingCard key={finding.id} finding={finding} project={project} />)}
          </div>
        )}
      </section>

      <TrustPanel signals={trustSignals} />

      <div className="focus-section-head"><h2>修复后验证闭环</h2></div>
      <RegressionClosurePanel
        record={record}
        regressionRunningMode={regressionRunningMode}
        regressionEligible={regressionEligible}
        onRunRegression={(mode) => void handleRegressionRun(mode)}
      />

      <TechnicalDiagnostics>
        <DiscoveryFunnelPanel funnel={record.discovery_funnel} report={record.discovery_funnel_report} />
        <ChainPositioningPanel positioning={record.discovery_chain_positioning} />
        <MainChainContractPanel record={record} />
        <section className="customer-secondary-grid" aria-label="交付口径">
          <article className="customer-secondary-card">
            <span className="customer-value-kicker">交付口径</span>
            <div className="customer-secondary-meta">
              <span><em>本轮缺陷</em><b>{currentScanDefects} 条</b></span>
              <span><em>缺陷货架</em><b>{familyShelfDefects} 条</b></span>
              <span><em>确认回执</em><b>{campaignConfirmed}</b></span>
            </div>
            <div className="customer-secondary-meta">
              {scopeFacts.map((fact) => (
                <span key={fact.label} data-tone={fact.tone} title={fact.note}><em>{fact.label}</em><b>{fact.val} 条</b></span>
              ))}
            </div>
            {(campaignCurrentRawFindings > 0 || currentScanFindings > currentScanDefects) && (
              <div className="customer-secondary-meta">
                <span><em>内部原始 finding（非客户交付）</em><b>{campaignCurrentRawFindings || Math.max(0, currentScanFindings - currentScanDefects)}</b></span>
                <span><em>口径说明</em><b>回执 {campaignConfirmed} → 本轮可交付 {currentScanDefects} → 当前正式范围 {familyShelfDefects}；原始 finding 仅供内部观测</b></span>
              </div>
            )}
          </article>
        </section>
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
