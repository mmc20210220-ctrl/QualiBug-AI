import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { emitScanCompleted, getCommercialAssets, isCustomerReadyFinding, usePipelineData } from '../api/data';
import { runRegression } from '../api/client';
import { useToast } from '../components/useToast';
import { buildReportData, renderReportHTML } from '../api/report';
import { formatDurationMs } from '../lib/display';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { AnimatedCounter } from '../components/AnimatedCounter';
import { ValueDashboard } from '../components/ValueDashboard';
import { TechnicalDiagnostics } from '../components/TechnicalDiagnostics';
import { RiskRing, ReleaseLight, Skeleton, StatePanel } from '../components/dashboard/DashboardPrimitives';
import {
  asRecord, asText, asNum, firstNum, formatScanTime,
  getSeverityWeight, getFindingModule, riskLevel, releaseDecision,
  campaignStatusLabel, campaignDetail,
  commercialHandoffLabel, trackerSyncLabel, deliveryPackageLabel,
  regressionGateLabel, regressionTrendLabel, releaseRecommendationLabel,
  getExecutiveHeadline, getGatePatchStatus, getMainChainContract,
  getMainChainSummary, getMainChainStages,
  getEvidenceNormalizationSummary, getEvidenceNormalizationReport,
  evidenceNormalizationItems, evidenceMissingEntries, evidenceMissingFieldLabel,
  evidenceItemTitle, evidenceItemAction, evidenceNormalizationLabel,
  mainChainStageLabel, mainChainStatusLabel, mainChainReadyLabel, gatePatchLabel,
  MAIN_CHAIN_STAGE_LABELS, FUNNEL_STAGE_LABELS,
  type JsonRecord,
} from '../lib/dashboard-utils';
import type { Finding, RegressionSummary } from '../types';

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
      const reportData = buildReportData({ projectName: asText(record.project_name) || project, industry: asText(record.industry), totalBugs: findings.length, beiScore: asNum(scores.bei), bdsScore: String(scores.bds || '0.0'), bcsScore: asNum(scores.bcs), runtimeProbes: asNum(asRecord(record.business_flow_summary).total), dbConfirmed: asNum(asRecord(record.db_verification).confirmed), findings, dbFindings: [] });
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

  if (loading) return (<div><div className="executive-hero"><div className="executive-hero-conclusion"><Skeleton h={20} w={100} br={10} /><div style={{marginTop:12}}><Skeleton h={32} w="70%" br={6} /></div><div style={{marginTop:8}}><Skeleton h={16} w="90%" /></div></div><div className="executive-hero-metrics">{[1,2,3,4].map((i) => <div key={i} className="executive-metric"><Skeleton h={36} w={60} br={6} /><Skeleton h={14} w={80} /></div>)}</div><div className="executive-hero-decision"><Skeleton h={80} w={120} br={12} /></div></div></div>);
  if (error && !data) return <StatePanel eyebrow="连接状态" title="后端暂时不可用" description={error} action={<button className="btn btn-primary" onClick={refetch}>重新连接</button>} />;
  if (!project) return <StatePanel eyebrow="开始使用" title="选择客户项目，查看检测价值" description="选择客户后，您将看到：已确认的问题清单、发布安全建议、AI 检测节省的人力成本量化，以及完整的证据链。" />;

  const record = asRecord(data);
  const commercialAssets = getCommercialAssets(record);
  const findings = ((record.defects || record.risks || []) as Finding[]).filter(isCustomerReadyFinding);
  const clues = ((record.clues || []) as Finding[]);
  const regressionSummary = asRecord(record.regression_summary) as unknown as RegressionSummary;
  const valueMetrics = asRecord(record.value_metrics);
  const scanMeta = asRecord(record.scan_meta);
  const formalCounts = asRecord(record.formal_count_projection);
  const benchmarkMetrics = asRecord(scanMeta.benchmark_metrics);
  const externalEvaluation = asRecord(Object.keys(asRecord(record.external_evaluation)).length > 0 ? record.external_evaluation : scanMeta.external_evaluation);
  const qualityClaimStatus = asText(record.quality_claim_status) || asText(scanMeta.quality_claim_status) || asText(externalEvaluation.measurement_status) || 'NOT_MEASURED';
  const externalMeasured = qualityClaimStatus === 'MEASURED' && asText(externalEvaluation.measurement_status) === 'MEASURED';
  const externalDisplay = asRecord(externalEvaluation.display);
  const qualitySuppressed = !externalMeasured || Boolean(externalDisplay.suppress_quality_score) || Boolean(benchmarkMetrics.commercial_quality_suppressed);
  const benchmarkActive = Boolean(benchmarkMetrics.benchmark_active) && !qualitySuppressed;
  const benchmarkFailed = asText(benchmarkMetrics.status) === 'FAILED_SAFE';
  const gatePatch = getGatePatchStatus(record);
  const gatePatchEnabled = Boolean(gatePatch.core_gate_direct);
  const mainChainContract = getMainChainContract(record);
  const mainChainSummary = getMainChainSummary(record, mainChainContract);
  const mainChainStages = getMainChainStages(mainChainContract);
  const hasMainChainContract = Object.keys(mainChainContract).length > 0 || Object.keys(mainChainSummary).length > 0;
  const mainChainReady = Boolean(mainChainSummary.chain_ready);
  const firstBlockedStage = asText(mainChainSummary.first_blocked_stage);
  const firstBlockedStageLabel = firstBlockedStage ? (MAIN_CHAIN_STAGE_LABELS[firstBlockedStage] || firstBlockedStage) : '暂无';
  const firstBlockedNextAction = asText(mainChainSummary.first_blocked_next_action) || '等待上报。';
  const evidenceNormalizationSummary = getEvidenceNormalizationSummary(record);
  const evidenceNormalizationReport = getEvidenceNormalizationReport(record);
  const evidenceNormalizationItemReports = evidenceNormalizationItems(evidenceNormalizationReport);
  const blockedEvidenceActionItems = evidenceNormalizationItemReports.filter((i) => i.normalized !== true);
  const evidenceMissingFields = evidenceMissingEntries(evidenceNormalizationSummary);
  const evidenceBlockedItemCount = asNum(evidenceNormalizationSummary.blocked_item_count, blockedEvidenceActionItems.length);
  const evidenceFullyNormalizedCount = asNum(evidenceNormalizationSummary.fully_normalized_count);
  const hasEvidenceNormalizationSummary = Object.keys(evidenceNormalizationSummary).length > 0 || evidenceNormalizationItemReports.length > 0;
  const campaign = asRecord(record.campaign);
  const continuousCampaign = asRecord(record.continuous_discovery_campaign);
  const campaignSummary = asRecord(continuousCampaign.summary);
  const campaignStatus = asText(campaign.campaign_status).toLowerCase();
  const campaignDeferredReason = asText(campaign.coverage_deferred_reason);
  const nextCampaignReason = asText(campaign.next_campaign_reason);
  const campaignScope = asText(campaign.scope_id);
  const campaignEnvironment = asText(campaign.environment_ref);
  const totalRiskCount = findings.length;
  const campaignAttempted = asNum(campaign.attempted_slice_count);
  const campaignConfirmed = firstNum(campaignSummary.current_campaign_confirmed_slice_count, campaignSummary.confirmed_slice_count, campaign.confirmed_slice_count);
  const p0Count = findings.filter((f) => f.severity === 'P0').length;
  const highPriorityCount = p0Count + findings.filter((f) => f.severity === 'P1').length;
  const currentScanDefects = asNum(formalCounts.formal_customer_deliverable_count, totalRiskCount);
  const familyShelfDefects = currentScanDefects;
  const currentScanP0Count = Math.min(p0Count, currentScanDefects);
  const currentScanHighPriorityCount = Math.min(highPriorityCount, currentScanDefects);
  const coverageGaps = Array.isArray(record.coverage_gaps) ? record.coverage_gaps.length : 0;
  const discoveryFunnel = asRecord(record.discovery_funnel);
  const pipelineHealth = asRecord(Object.keys(asRecord(record.pipeline_health)).length > 0 ? record.pipeline_health : Object.keys(asRecord(discoveryFunnel.pipeline_health)).length > 0 ? discoveryFunnel.pipeline_health : scanMeta.pipeline_health);
  const pipelineHealthStatus = asText(pipelineHealth.status) || asText(scanMeta.pipeline_health_status);
  const pipelineFailedSafe = pipelineHealthStatus === 'FAILED_SAFE';
  const pipelineBlocked = pipelineHealthStatus === 'BLOCKED';
  const pipelineUnhealthy = pipelineFailedSafe || pipelineBlocked;
  const funnelStages = Array.isArray(discoveryFunnel.stages) ? discoveryFunnel.stages.filter((i): i is JsonRecord => i !== null && typeof i === 'object' && !Array.isArray(i)) : [];
  const funnelBlockers = Array.isArray(discoveryFunnel.top_blocking_reasons) ? discoveryFunnel.top_blocking_reasons.filter((i): i is JsonRecord => i !== null && typeof i === 'object' && !Array.isArray(i)) : [];
  const hasDiscoveryFunnel = funnelStages.length > 0 || Boolean(asText(discoveryFunnel.explanation));
  const funnelValidated = asNum(discoveryFunnel.validated_bug_count);
  const funnelPending = asNum(discoveryFunnel.pending_finding_count);
  const funnelCandidates = asNum(discoveryFunnel.candidate_count);
  const governanceNeedsAction = campaignStatus === 'blocked' || campaignStatus === 'coverage_deferred' || pipelineUnhealthy;
  const clueCount = clues.length;
  const evidenceTrust = asNum(valueMetrics.evidence_trust_score, 0);
  const modules = Array.from(new Set(findings.map(getFindingModule).filter(Boolean)));
  const modulesCount = modules.length;
  const regressionCovered = asNum(regressionSummary.covered_defect_count);
  const regressionFailed = asNum(regressionSummary.failed_defect_count);
  const regressionPending = asNum(regressionSummary.pending_defect_count);
  const regressionPassed = asNum(regressionSummary.passed_defect_count);
  const regressionRunAt = asText(regressionSummary.latest_run?.generated_at);
  const regressionGate = regressionGateLabel(asText(regressionSummary.latest_run?.gate_status));
  const regressionHasLinkedDefects = regressionCovered > 0 || regressionPassed > 0 || regressionFailed > 0 || regressionPending > 0;
  const regressionGateDisplay = regressionHasLinkedDefects ? regressionGate : '回归待观察';
  const regressionTrend = regressionTrendLabel(asText(regressionSummary.trend_direction));
  const regressionHistoryRunCount = asNum(regressionSummary.history_run_count);
  const regressionValidationSummary = asRecord(regressionSummary.validation_summary);
  const regressionDoubleRunVerified = Boolean(regressionValidationSummary.double_run_verified);
  const releaseRecommendation = releaseRecommendationLabel(asText(regressionSummary.release_recommendation), asText(regressionSummary.release_recommendation_label));
  const releaseRecommendationReason = asText(regressionSummary.release_recommendation_reason);
  const customerDeliveryReadiness = asText(regressionSummary.customer_delivery_readiness_label) || asText(regressionSummary.customer_delivery_readiness) || '持续观察中';
  const hasMaterializedMetrics = totalRiskCount > 0 || clueCount > 0 || asNum(asRecord(record.business_flow_summary).total, 0) > 0 || Boolean(campaignStatus) || coverageGaps > 0 || hasMainChainContract || hasEvidenceNormalizationSummary;
  const topFindings = [...findings].sort((a, b) => { const sg = getSeverityWeight(b.severity) - getSeverityWeight(a.severity); return sg !== 0 ? sg : (b.evidence_quality?.score || 0) - (a.evidence_quality?.score || 0); }).slice(0, 3);
  const focusFindings = currentScanDefects > 0 ? topFindings : [];
  const executiveHeadline = getExecutiveHeadline(currentScanDefects, familyShelfDefects, currentScanP0Count, clueCount, campaignStatus, campaignDeferredReason);
  const conclusion = pipelineFailedSafe ? '检测异常（非"无问题"）' : pipelineBlocked ? '检测执行被阻断' : campaignStatus === 'blocked' ? '检测暂停' : campaignStatus === 'coverage_deferred' ? '部分范围待后续检测' : currentScanP0Count > 0 ? '存在阻断发布问题' : currentScanDefects > 0 ? '建议进入整改验收' : '当前未发现阻断性问题';
  const level = riskLevel(conclusion);
  const decision = releaseDecision(currentScanP0Count, currentScanDefects, pipelineUnhealthy, campaignStatus === 'blocked');
  const aiTestPoints = asNum(valueMetrics.ai_equivalent_test_points, asNum(asRecord(record.business_flow_summary).total, 0));
  const savedHours = Math.round(aiTestPoints * 0.5);
  const hasValueData = Boolean(valueMetrics.executive_message || aiTestPoints > 0 || evidenceTrust > 0);

  if (!hasMaterializedMetrics) {
    return (<div><div className="page-header"><div><h1>{asText(record.project_name) || project} · 价值总览</h1><p>当前项目还没有形成真实检测数据。</p></div></div>
      <section className="empty-value-promise"><h2>运行首次检测后，您将看到：</h2><div className="empty-value-grid"><div className="empty-value-card"><strong>已确认问题清单</strong><span>每个问题都有原始证据和复现路径</span></div><div className="empty-value-card"><strong>发布安全建议</strong><span>基于真实检测结果的发布决策参考</span></div><div className="empty-value-card"><strong>价值量化报告</strong><span>AI 检测节省的人力成本和时间</span></div><div className="empty-value-card"><strong>覆盖度分析</strong><span>业务模块覆盖和风险分布全景</span></div></div><button className="btn btn-primary" onClick={() => navigateToProjectPath('/campaigns', project)}>启动首次检测</button></section></div>);
  }

  return (
    <div className="customer-results-page">
      <section className="executive-hero mb-4">
        <div className="executive-hero-conclusion">
          <span className="executive-eyebrow">{asText(record.project_name) || project}</span>
          <div className="executive-conclusion-row"><RiskRing level={level} /><div><h1>{conclusion}</h1><p>{executiveHeadline}</p></div></div>
          <div className="executive-meta"><span>最近检测 {formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}</span><span>耗时 {formatDurationMs(asNum(scanMeta.total_ms))}</span><span>结论可靠度 {evidenceTrust > 0 ? `${evidenceTrust}%` : '待评估'}</span></div>
        </div>
        <div className="executive-hero-metrics">
          <div className="executive-metric"><AnimatedCounter value={currentScanDefects} className="executive-metric-value" /><span>已确认问题</span></div>
          <div className="executive-metric"><AnimatedCounter value={currentScanP0Count} className="executive-metric-value danger" /><span>阻断发布</span></div>
          <div className="executive-metric"><AnimatedCounter value={aiTestPoints} className="executive-metric-value" /><span>等效测试点</span></div>
          <div className="executive-metric"><AnimatedCounter value={modulesCount} className="executive-metric-value" /><span>覆盖模块</span></div>
        </div>
        <div className="executive-hero-decision"><ReleaseLight color={decision.color} label={decision.label} advice={decision.advice} /></div>
      </section>

      {hasValueData && <ValueDashboard value={{ executive_message: asText(valueMetrics.executive_message) || `AI 已完成 ${aiTestPoints} 个等效测试点的验证，相当于人工团队约 ${savedHours} 小时的工作量。`, ai_equivalent_test_points: aiTestPoints, evidence_trust_score: evidenceTrust, explored_behavior_paths: asNum(valueMetrics.explored_behavior_paths, modulesCount), blocked_risk_count: currentScanP0Count + currentScanHighPriorityCount, capability_families: asNum(valueMetrics.capability_families, 0), bug_families: asNum(valueMetrics.bug_families, 0), decision_cards: [{ role: 'CTO / 技术VP', title: '发布决策', value: decision.label, detail: decision.advice }, { role: '测试经理', title: '效率提升', value: `节省 ${savedHours}h`, detail: `AI 30分钟完成人工团队约 ${Math.max(1, Math.round(savedHours / 8))} 天的验证工作` }, { role: '项目经理', title: '风险拦截', value: `${currentScanP0Count} 个 P0`, detail: currentScanP0Count > 0 ? '阻断性问题已拦截，避免流入生产环境' : '当前无阻断性问题' }] }} />}

      <div className="customer-summary-grid mb-4">
        {[
          { label: '已确认问题', val: currentScanDefects, tone: currentScanDefects > 0 ? 'primary' : 'neutral', note: currentScanDefects > 0 ? `累计 ${familyShelfDefects} 个` : '当前没有已确认问题' },
          { label: '阻断发布', val: currentScanP0Count, tone: currentScanP0Count > 0 ? 'danger' : 'neutral', note: currentScanP0Count > 0 ? '需要立即处理' : '当前无阻断项' },
          { label: '结论可靠度', val: evidenceTrust > 0 ? `${evidenceTrust}%` : '待评估', tone: evidenceTrust >= 80 ? 'success' : evidenceTrust > 0 ? 'warning' : 'neutral', note: modulesCount > 0 ? `已覆盖 ${modulesCount} 个业务模块` : '等待检测完成' },
          ...(regressionCovered > 0 ? [{ label: '回归验证', val: regressionGate, tone: regressionFailed > 0 ? 'danger' : regressionPending > 0 ? 'warning' : 'success', note: `已覆盖 ${regressionCovered} 个问题` }] : []),
          ...(releaseRecommendation ? [{ label: '发布建议', val: releaseRecommendation, tone: asText(regressionSummary.release_recommendation) === 'block_release' ? 'danger' : asText(regressionSummary.release_recommendation) === 'candidate_release' ? 'success' : 'warning', note: releaseRecommendationReason || customerDeliveryReadiness }] : []),
          ...(commercialAssets ? [{ label: '交付状态', val: deliveryPackageLabel(commercialAssets), tone: commercialAssets.delivery_package.status === 'created' ? 'success' : 'warning', note: trackerSyncLabel(commercialAssets) }] : []),
        ].map((item) => (<article key={item.label} className={`customer-summary-card tone-${item.tone}`}><span>{item.label}</span><strong>{item.val}</strong><small>{item.note}</small></article>))}
      </div>

      <section className="customer-focus-section mb-4">
        <div className="customer-section-head"><div><span className="panel-kicker">重点关注</span><h2>优先处理的问题</h2></div><button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看完整清单</button></div>
        {focusFindings.length === 0 ? (
          <section className="findings-empty-state compact"><span className="findings-empty-kicker">当前结论</span><h3>{governanceNeedsAction ? campaignStatusLabel(campaignStatus) : '当前没有需要优先处理的问题'}</h3><p>{governanceNeedsAction ? campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason) : clueCount > 0 ? `本轮仅有 ${clueCount} 条内部线索仍在补证。` : '当前没有已确认问题。'}</p></section>
        ) : (
          <div className="customer-focus-list">{focusFindings.map((f) => (
            <article key={f.id} className={`customer-focus-card severity-${f.severity.toLowerCase()}`}>
              <div className="customer-focus-head"><span className={`severity-dot ${f.severity.toLowerCase()}`} /><span className={`severity ${f.severity.toLowerCase()}`}>{f.severity}</span><strong>{f.title}</strong></div>
              <p>{f.business_summary || f.business_impact?.summary || f.actual || '该问题已形成确认结论。'}</p>
              <div className="customer-focus-meta"><span><em>影响模块</em><b>{getFindingModule(f)}</b></span><span><em>证据状态</em><b>{f.evidence_quality?.label || '已归档'}</b></span><span><em>复现稳定性</em><b>{f.proof?.repro_rate ?? 0}%</b></span></div>
            </article>
          ))}</div>
        )}
      </section>

      <section className="customer-actions-bar mb-4">
        <button className="btn btn-primary" onClick={() => navigateToProjectPath('/findings', project)}>查看问题清单</button>
        <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button>
        <button className="btn btn-secondary" onClick={handleExport}>导出价值报告</button>
        {(regressionCovered > 0 || regressionSummary.suite_exists) && <><button className="btn btn-secondary" onClick={() => void handleRegressionRun('release')} disabled={regressionRunningMode !== ''}>{regressionRunningMode === 'release' ? 'Release 回归中' : '执行 Release 回归'}</button><button className="btn btn-secondary" onClick={() => void handleRegressionRun('smoke')} disabled={regressionRunningMode !== ''}>{regressionRunningMode === 'smoke' ? 'Smoke 回归中' : '执行 Smoke 回归'}</button></>}
      </section>

      <TechnicalDiagnostics>
        <section className="customer-secondary-grid">
          <article className="customer-secondary-card"><span className="customer-value-kicker">本轮检测说明</span><div className="customer-secondary-meta"><span><em>最近扫描</em><b>{formatScanTime(asText(scanMeta.last_scan_at) || asText(record.updated_at))}</b></span><span><em>本轮耗时</em><b>{formatDurationMs(asNum(scanMeta.total_ms))}</b></span><span><em>质量门</em><b>{gatePatchLabel(gatePatchEnabled)}</b></span><span><em>检测流程</em><b>{mainChainReadyLabel(mainChainReady, hasMainChainContract)}</b></span><span><em>证据标准化</em><b>{evidenceNormalizationLabel(evidenceNormalizationSummary)}</b></span></div></article>
          {campaignStatus && <article className="customer-secondary-card"><span className="customer-value-kicker">检测治理</span><h3>{campaignStatusLabel(campaignStatus)}</h3><p>{campaignDetail(campaignStatus, campaignDeferredReason, nextCampaignReason)}</p><div className="customer-secondary-meta"><span><em>范围</em><b>{campaignScope || '待登记'}</b></span><span><em>环境</em><b>{campaignEnvironment || '待登记'}</b></span><span><em>确认回执</em><b>{campaignConfirmed}/{campaignAttempted || 0}</b></span><span><em>本轮问题</em><b>{currentScanDefects} 条</b></span><span><em>覆盖缺口</em><b>{coverageGaps}</b></span></div></article>}
          <article className="customer-secondary-card"><span className="customer-value-kicker">检测流程状态</span><h3>{mainChainReadyLabel(mainChainReady, hasMainChainContract)}</h3><p>{hasMainChainContract ? (mainChainReady ? '全部阶段已完成。' : `第一断点：${firstBlockedStageLabel}。下一步：${firstBlockedNextAction}`) : '尚未返回检测流程数据。'}</p><div className="customer-secondary-meta">{mainChainStages.length > 0 ? mainChainStages.map((s) => <span key={`${asText(s.stage)}-${asText(s.status)}`}><em>{mainChainStageLabel(s)}</em><b>{mainChainStatusLabel(s)}</b></span>) : <><span><em>通过</em><b>{asNum(mainChainSummary.passed_stage_count)}</b></span><span><em>部分</em><b>{asNum(mainChainSummary.partial_stage_count)}</b></span><span><em>缺失</em><b>{asNum(mainChainSummary.missing_stage_count)}</b></span></>}</div></article>
          {hasEvidenceNormalizationSummary && <article className="customer-secondary-card"><span className="customer-value-kicker">证据标准化</span><h3>{evidenceNormalizationLabel(evidenceNormalizationSummary)}</h3><p>{evidenceMissingFields.length > 0 ? `仍缺字段：${evidenceMissingFields.map(([f, c]) => `${evidenceMissingFieldLabel(f)}×${c}`).join('、')}。` : `已标准化：${evidenceFullyNormalizedCount} 项。`}</p><div className="customer-secondary-meta"><span><em>已标准化</em><b>{evidenceFullyNormalizedCount}</b></span><span><em>仍阻断</em><b>{evidenceBlockedItemCount}</b></span></div>{blockedEvidenceActionItems.length > 0 && <div className="customer-secondary-meta">{blockedEvidenceActionItems.slice(0, 3).map((i) => <span key={`${evidenceItemTitle(i)}-${asText(i.trace_id)}`}><em>{evidenceItemTitle(i)}</em><b>{evidenceItemAction(i)}</b></span>)}</div>}</article>}
          {pipelineUnhealthy && <article className="customer-secondary-card"><span className="customer-value-kicker">检测链路健康</span><h3>{pipelineFailedSafe ? '检测异常：空结果 ≠ 无问题' : '执行被阻断'}</h3><p>{asText(pipelineHealth.operator_note) || '检测链路存在问题。'}</p><div className="customer-secondary-meta"><span><em>链路状态</em><b>{pipelineHealthStatus || '未知'}</b></span><span><em>执行状态</em><b>{asText(pipelineHealth.execution_status) || '—'}</b></span></div></article>}
          {hasDiscoveryFunnel && <article className="customer-secondary-card"><span className="customer-value-kicker">发现漏斗</span><h3>已验证 {funnelValidated} · 待确认 {funnelPending} · 候选 {funnelCandidates}</h3><p>{asText(discoveryFunnel.explanation) || '本轮漏斗已生成。'}</p><div className="customer-secondary-meta">{funnelStages.map((s) => { const name = asText(s.name); const label = FUNNEL_STAGE_LABELS[name] || name; const input = asNum(s.input); const output = asNum(s.output); const conv = asNum(s.conversion); const pct = conv > 0 ? `${Math.round(conv * 100)}%` : (input > 0 ? '0%' : '—'); return <span key={name || label}><em>{label}</em><b>{output}/{input}（{pct}）</b></span>; })}</div>{funnelBlockers.length > 0 && <div className="customer-secondary-meta">{funnelBlockers.slice(0, 5).map((i) => <span key={`${asText(i.reason)}-${asNum(i.count)}`}><em>阻断 {asText(i.reason) || '未知'}</em><b>{asNum(i.count)}</b></span>)}</div>}</article>}
          {qualitySuppressed && <article className="customer-secondary-card"><span className="customer-value-kicker">外部质量评测</span><h3>{asText(externalDisplay.quality_label) || '尚未完成外部质量评测'}</h3><p>商业召回率/精度仅来自外部评测。当前状态为 {qualityClaimStatus}。</p></article>}
          {benchmarkActive && !benchmarkFailed && <article className="customer-secondary-card"><span className="customer-value-kicker">检测能力 Benchmark</span><h3>召回率 {Math.round(asNum(benchmarkMetrics.recall) * 100)}% · 精度 {Math.round(asNum(benchmarkMetrics.precision) * 100)}%</h3><div className="customer-secondary-meta"><span><em>F1</em><b>{Math.round(asNum(benchmarkMetrics.f1_score) * 100)}%</b></span><span><em>误报率</em><b>{Math.round(asNum(benchmarkMetrics.false_positive_rate) * 100)}%</b></span><span><em>GT Bug</em><b>{asNum(benchmarkMetrics.ground_truth_bug_count)}</b></span><span><em>检出</em><b>{asNum(benchmarkMetrics.true_positives)}</b></span></div></article>}
          {commercialAssets && <article className="customer-secondary-card"><span className="customer-value-kicker">商业交付资产</span><h3>{commercialHandoffLabel(commercialAssets)}</h3><div className="customer-secondary-meta"><span><em>交付包</em><b>{deliveryPackageLabel(commercialAssets)}</b></span><span><em>Tracker</em><b>{trackerSyncLabel(commercialAssets)}</b></span><span><em>客户复验</em><b>{commercialAssets.customer_ready_reproduction_count}</b></span></div></article>}
          {(regressionCovered > 0 || regressionSummary.suite_exists) && <article className="customer-secondary-card"><span className="customer-value-kicker">回归验证</span><h3>{regressionGateDisplay}</h3><div className="customer-secondary-meta"><span><em>已覆盖</em><b>{regressionCovered}</b></span><span><em>通过</em><b>{regressionPassed}</b></span><span><em>失败</em><b>{regressionFailed}</b></span><span><em>待执行</em><b>{regressionPending}</b></span><span><em>最近回归</em><b>{regressionRunAt ? formatScanTime(regressionRunAt) : '暂无'}</b></span></div></article>}
          {(regressionHistoryRunCount > 0) && <article className="customer-secondary-card"><span className="customer-value-kicker">回归趋势</span><h3>{regressionTrend}</h3><div className="customer-secondary-meta"><span><em>历史轮次</em><b>{regressionHistoryRunCount}</b></span><span><em>趋势</em><b>{regressionTrend}</b></span><span><em>发布建议</em><b>{releaseRecommendation}</b></span><span><em>双轮验真</em><b>{regressionDoubleRunVerified ? '已满足' : '未满足'}</b></span></div></article>}
          {clueCount > 0 && <article className="customer-secondary-card"><span className="customer-value-kicker">内部待跟进</span><h3>{clueCount} 条线索仍在补证</h3><p>只供内部运营使用，不进入客户问题交付。</p><button className="btn btn-secondary" onClick={() => navigateToProjectPath('/clues', project)}>进入内部工作台</button></article>}
        </section>
      </TechnicalDiagnostics>
    </div>
  );
}

export default Dashboard;
