import { lazy, Suspense, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { emitScanCompleted, hasRealReplayAsset, isCustomerReadyFinding, useFindingsData } from '../api/data';
import { runRegression } from '../api/client';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { EvidenceTimeline } from '../components/EvidenceTimeline';
import type { CommercialAssets, Finding } from '../types';

const ReplayViewer = lazy(() => import('../components/ReplayViewer'));
type FindingFilter = 'all' | 'P0' | 'P1' | 'P2' | string;

function getFilterDisplayName(filter: FindingFilter): string {
  if (filter === 'all') return '全部';
  if (filter === 'P0') return 'P0 严重缺陷';
  if (filter === 'P1') return 'P1 一般缺陷';
  if (filter === 'P2') return 'P2 轻微缺陷';
  return filter;
}

function moduleName(finding: Finding): string {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '未归类').trim() || '未归类';
}

function evidenceScope(finding: Finding): string {
  return String(finding.investigation_guidance?.primary_area || moduleName(finding)).trim() || '待归属';
}

function defectTag(finding: Finding): string {
  if (finding.severity === 'P0') return 'P0 阻塞事实';
  if (finding.severity === 'P1') return 'P1 已确认事实';
  return 'P2 已记录事实';
}

function regressionStatus(finding: Finding): string {
  return String(finding.regression?.latest_status_label || (finding.regression?.included_in_suite ? '待执行回归' : '未纳入回归')).trim() || '未纳入回归';
}

function lifecycleStatus(finding: Finding): string {
  return String(finding.regression?.lifecycle_label || (finding.regression?.included_in_suite ? '待回归' : '待纳入回归')).trim() || '待纳入回归';
}

function closureRequirement(finding: Finding): string {
  const regression = finding.regression;
  const latest = String(regression?.latest_status || '').toLowerCase();
  if (latest === 'passed') return '客户修复后回归已通过，本缺陷可进入闭环确认。';
  if (latest === 'failed') return '客户修复后回归仍失败，本缺陷尚未闭环。';
  if (latest === 'needs_review') return '客户修复后回归需要人工复核，尚不能声明闭环。';
  if (regression?.included_in_suite) return '已纳入回归套件；客户修复后执行回归验证是否闭环。';
  return '尚未纳入回归套件；不能声明修复闭环。';
}

function normalizedCommercialStatus(status: string): string {
  const value = status.trim();
  if (['fail', 'failed', 'block_release', 'blocked_by_release_gate'].includes(value)) return 'fail';
  if (['pending', 'manual_approval_required', 'hold_for_validation'].includes(value)) return 'pending';
  if (['pass', 'passed', 'candidate_release', 'release_gate_passed'].includes(value)) return 'pass';
  return value;
}

function commercialReleaseOverall(assets: CommercialAssets | null): string {
  return normalizedCommercialStatus(String(
    assets?.release_gate?.overall_status ||
    assets?.release_gate_overall_status ||
    assets?.delivery_package.release_gate_overall_status ||
    assets?.delivery_package.release_verdict ||
    assets?.tracker_sync.payload_gate_status ||
    '',
  ));
}

function commercialReleaseLabel(status: string): string {
  if (status === 'fail') return '发布门禁阻塞';
  if (status === 'pending') return '发布门禁待处理';
  if (status === 'pass') return '发布门禁通过';
  return '发布门禁待同步';
}

function commercialReleaseTone(status: string): string {
  if (status === 'fail') return 'danger';
  if (status === 'pending') return 'warning';
  if (status === 'pass') return 'success';
  return 'neutral';
}

function handoffReadinessLabel(assets: CommercialAssets | null): string {
  if (!assets) return '交付状态待同步';
  if (assets.commercial_handoff.safe_for_customer) return '商业交付可进入验收';
  const status = assets.commercial_handoff.acceptance_status || assets.tracker_sync.payload_status || assets.delivery_package.status;
  if (status === 'blocked_by_release_gate') return '商业交付被发布门禁阻塞';
  if (status === 'hold_for_validation') return '商业交付待复核';
  if (status) return `商业交付状态：${status}`;
  return '商业交付尚未确认安全';
}

function commercialReleaseDetail(assets: CommercialAssets | null): string {
  const primaryCheck = assets?.release_gate?.checks?.[0];
  return String(
    primaryCheck?.detail ||
    assets?.delivery_package.release_gate_block_reason ||
    assets?.release_gate_honesty_rule ||
    assets?.release_gate?.honesty_rule ||
    '发布门禁状态来自后端 commercial_assets.release_gate；商业交付是否安全还需同时满足 handoff 状态。',
  ).trim();
}

export function Findings() {
  usePageTitle('行为验证');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const { findings, clues, commercialAssets, loading, error, refetch } = useFindingsData(project);
  const toast = useToast();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<FindingFilter>('all');
  const [replayFinding, setReplayFinding] = useState<Finding | null>(null);
  const [regressionRunning, setRegressionRunning] = useState(false);

  const confirmed = findings.filter(isCustomerReadyFinding);
  const bySeverity = {
    P0: confirmed.filter((item) => item.severity === 'P0').length,
    P1: confirmed.filter((item) => item.severity === 'P1').length,
    P2: confirmed.filter((item) => item.severity === 'P2').length,
  };
  const validatedCount = confirmed.filter((item) => item.evidence_quality.level === 'validated').length;
  const familyStats = new Map<string, { label: string; count: number }>();
  for (const finding of confirmed) {
    const family = finding.defect_family || 'other';
    const existing = familyStats.get(family);
    familyStats.set(family, { label: finding.defect_family_label || family, count: (existing?.count || 0) + 1 });
  }
  const filters: Array<{ label: string; value: FindingFilter }> = [
    { label: `全部 (${confirmed.length})`, value: 'all' },
    ...Array.from(familyStats.entries()).map(([value, meta]) => ({ label: `${meta.label} (${meta.count})`, value })),
    { label: `P0 (${bySeverity.P0})`, value: 'P0' },
    { label: `P1 (${bySeverity.P1})`, value: 'P1' },
    { label: `P2 (${bySeverity.P2})`, value: 'P2' },
  ];
  const display = confirmed.filter((finding) => {
    if (filter === 'all') return true;
    if (filter === 'P0' || filter === 'P1' || filter === 'P2') return finding.severity === filter;
    return finding.defect_family === filter;
  });
  const topModules = Array.from(new Set(confirmed.map(moduleName))).slice(0, 3);
  const commercialOverall = commercialReleaseOverall(commercialAssets);
  const commercialGate = commercialAssets?.release_gate;
  const handoffReady = Boolean(commercialAssets?.commercial_handoff.safe_for_customer);
  const handoffLabel = handoffReadinessLabel(commercialAssets);
  const runReleaseRegression = async (): Promise<void> => {
    if (!project) return;
    setRegressionRunning(true);
    try {
      toast.show('正在执行 Release 回归...', 'info');
      const result = await runRegression(project, { mode: 'release' });
      emitScanCompleted(project);
      await refetch();
      const gateStatus = String(result.ci_feedback?.gate_status || 'unknown');
      const failedCount = Number(result.summary?.failed_count || 0);
      toast.show(
        `Release 回归完成：${gateStatus}${failedCount > 0 ? `，失败 ${failedCount} 项` : ''}`,
        gateStatus === 'failed' ? 'danger' : gateStatus === 'passed' ? 'success' : 'warning',
      );
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '回归执行失败';
      toast.show(message, 'danger');
    } finally {
      setRegressionRunning(false);
    }
  };

  return (
    <div>
      <section className="customer-showcase findings-showcase mb-4">
        <div className="customer-showcase-main">
          <span className="panel-kicker">客户缺陷闭环清单</span>
          <h1>{confirmed.length > 0 ? `当前已确认 ${confirmed.length} 个可交付缺陷，等待客户修复后回归验证。` : '当前没有可交付缺陷。'}</h1>
          <p>{confirmed.length > 0 ? `仅展示已执行、可复现且证据完整的缺陷。平台只提供事实证据和回归验证，不提供修复方案。当前重点涉及 ${topModules.length ? topModules.join('、') : '多个模块'}。` : clues.length > 0 ? `当前有 ${clues.length} 条内部线索正在补证，它们不会作为已确认缺陷交付。` : '当前项目尚未形成具备真实运行证据的已确认缺陷。'}</p>
          <div className="page-summary-strip"><span className="summary-pill strong">可交付缺陷 {confirmed.length}</span><span className="summary-pill">P0 阻塞 {bySeverity.P0}</span><span className="summary-pill">P1 已确认 {bySeverity.P1}</span><span className="summary-pill">待补证线索 {clues.length}</span>{commercialOverall && <span className="summary-pill">发布门禁 {commercialReleaseLabel(commercialOverall)}</span>}<span className="summary-pill">交付安全 {handoffReady ? '是' : '否'}</span></div>
          <div className="customer-showcase-actions"><button className="btn btn-primary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button><button className="btn btn-secondary" onClick={() => void runReleaseRegression()} disabled={regressionRunning}>{regressionRunning ? 'Release 回归中' : '执行 Release 回归'}</button>{clues.length > 0 && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/clues', project)}>查看内部线索</button>}{error && <button className="btn btn-secondary" onClick={refetch}>重新加载</button>}</div>
        </div>
        <div className="customer-showcase-side">
          {commercialOverall && (
            <div className={`customer-status-card ${commercialReleaseTone(commercialOverall)}`}>
              <span>商业交付 Release Gate</span>
              <strong>{commercialReleaseLabel(commercialOverall)}</strong>
              <p>{commercialReleaseDetail(commercialAssets)}</p>
            </div>
          )}
          <div className={`customer-status-card ${handoffReady ? 'success' : commercialOverall === 'fail' ? 'danger' : 'warning'}`}>
            <span>商业交付 Handoff</span>
            <strong>{handoffLabel}</strong>
            <p>{handoffReady ? '商业交付安全状态由后端 handoff 明确放行。' : '即使发布门禁通过，也必须等待 handoff 明确 safe_for_customer 后才能作为完整商业交付。'}</p>
          </div>
          <div className={`customer-status-card ${bySeverity.P0 > 0 ? 'danger' : confirmed.length > 0 ? 'warning' : 'success'}`}>
            <span>闭环状态</span>
            <strong>{bySeverity.P0 > 0 ? '存在 P0 阻塞事实' : confirmed.length > 0 ? '等待客户修复后回归' : '当前无需缺陷闭环'}</strong>
            <p>{bySeverity.P0 > 0 ? `${bySeverity.P0} 个 P0 缺陷已确认；平台不提供修复方案，只在客户修复后验证是否闭环。` : confirmed.length > 0 ? `${validatedCount} 条缺陷已达到高质量证据标准，等待后续回归验证。` : '不要将未验证候选或模拟结果交给客户。'}</p>
          </div>
          <div className="customer-status-meta">
            <span><em>证据模块</em><b>{topModules.length ? topModules.join('、') : '暂无'}</b></span>
            <span><em>证据达标</em><b>{validatedCount}/{confirmed.length}</b></span>
            <span><em>覆盖类型</em><b>{familyStats.size}</b></span>
            {commercialOverall && <span><em>发布 verdict</em><b>{commercialOverall}</b></span>}
            <span><em>handoff</em><b>{handoffReady ? 'safe' : 'not safe'}</b></span>
            {commercialGate && <span><em>门禁项</em><b>{commercialGate.blocking_check_count || 0}/{commercialGate.pending_check_count || 0}</b></span>}
          </div>
        </div>
      </section>

      {confirmed.length > 0 && <><div className="customer-summary-grid findings-summary-grid mb-4">{[
        { label: 'P0 阻塞', value: bySeverity.P0, tone: 'danger', note: bySeverity.P0 ? '已确认高风险事实' : '当前无 P0 阻塞事实' },
        { label: 'P1 已确认', value: bySeverity.P1, tone: 'warning', note: bySeverity.P1 ? '已进入缺陷闭环清单' : '当前无 P1 积压' },
        { label: '证据达标', value: validatedCount, tone: 'primary', note: '满足客户复验与验收口径' },
        { label: '发布门禁', value: commercialOverall ? commercialReleaseLabel(commercialOverall) : '待同步', tone: commercialOverall === 'fail' ? 'danger' : commercialOverall === 'pending' ? 'warning' : commercialOverall === 'pass' ? 'success' : 'neutral', note: commercialAssets?.delivery_package.release_gate_block_reason || commercialAssets?.release_recommendation || '来自 commercial_assets.release_gate' },
        { label: '交付安全', value: handoffReady ? '已放行' : '未放行', tone: handoffReady ? 'success' : 'warning', note: handoffLabel },
        { label: '涉及模块', value: familyStats.size, tone: 'neutral', note: '已形成明确归类与证据定位' },
      ].map((item) => <article key={item.label} className={`customer-summary-card tone-${item.tone}`}><span>{item.label}</span><strong>{item.value}</strong><small>{item.note}</small></article>)}</div>
      <div className="page-header findings-page-header"><div><span className="panel-kicker">清单视图</span><h1>行为验证</h1><p>已确认缺陷只提供业务影响、事实证据、真实复验入口与回归执行动作；不提供修复建议或修复方案。</p></div><div className="findings-toolbar-note">当前展示 {getFilterDisplayName(filter)} 缺陷闭环项</div></div>
      <div className="filters behavior-filters findings-filter-bar mb-4">{filters.map((item) => <button key={item.value} onClick={() => setFilter(item.value)} className={`filter${filter === item.value ? ' active' : ''}`}>{item.label}</button>)}</div></>}

      {loading && <div className="state-panel"><div className="spinner spinner-centered" /><p>正在整理可交付缺陷...</p></div>}
      {!loading && error && display.length === 0 && <section className="findings-empty-state danger"><span className="findings-empty-kicker">连接异常</span><h3>缺陷数据暂时不可用</h3><p>{error}</p><button className="btn btn-primary" onClick={refetch}>重新连接</button></section>}
      {!loading && !error && display.length === 0 && <section className="findings-empty-state compact"><span className="findings-empty-kicker">当前结论</span><h3>{filter === 'all' ? '当前暂不向客户展示缺陷清单' : '当前没有对应风险'}</h3><p>{clues.length > 0 ? `本轮存在 ${clues.length} 条待验证线索，尚不足以进入客户交付。` : '系统尚未检测到具备真实运行证据的可交付缺陷。'}</p><div className="findings-repro-actions"><button className="btn btn-primary" onClick={() => navigateToProjectPath('/dashboard', project)}>前往总览启动扫描</button>{clues.length > 0 && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/clues', project)}>查看待验证线索</button>}</div></section>}

      {display.map((finding) => {
        const open = expandedId === finding.id;
        const reproduction = finding.reproduction;
        const quality = finding.evidence_quality;
        const investigation = finding.investigation_guidance;
        const replayCommand = quality.curl_command || '';
        const canReplay = hasRealReplayAsset(finding) && Boolean(replayCommand);
        const impact = finding.business_impact.summary || finding.business_summary || finding.actual || '该问题已形成可交付缺陷。';
        const closure = closureRequirement(finding);
        const affected = finding.affected_count || finding.affected_instances?.length || 0;
        const regression = finding.regression;
        return <article key={finding.id} className={`findings-delivery-card severity-${finding.severity.toLowerCase()}${open ? ' open' : ''}`}>
          <div className="findings-delivery-head" onClick={() => setExpandedId(open ? null : finding.id)}><div className="findings-delivery-title"><div className="findings-delivery-badges"><span className={`severity ${finding.severity.toLowerCase()}`}>{finding.severity}</span><span className="findings-delivery-badge">{defectTag(finding)}</span><span className="findings-delivery-badge subtle">{moduleName(finding)}</span><span className={`findings-delivery-badge${regression?.latest_status === 'failed' ? ' danger' : regression?.latest_status === 'passed' ? ' success' : ''}`}>{regressionStatus(finding)}</span></div><h2>{finding.title}</h2><p>{impact}</p></div><div className="findings-delivery-meta"><span><em>证据归属</em><b>{evidenceScope(finding)}</b></span><span><em>证据状态</em><b>{quality.label || '已归档'}</b></span><span><em>复现稳定性</em><b>{finding.proof.repro_rate}%</b></span><button type="button" className="btn btn-secondary btn-sm" onClick={(event) => { event.stopPropagation(); setExpandedId(open ? null : finding.id); }}>{open ? '收起细节' : '查看细节'}</button></div></div>
          <div className="findings-delivery-strip"><div className="findings-delivery-strip-item"><span>业务影响</span><strong>{impact}</strong></div><div className="findings-delivery-strip-item"><span>闭环状态</span><strong>{closure}</strong></div><div className="findings-delivery-strip-item"><span>影响范围</span><strong>{affected ? `已命中 ${affected} 个业务实例` : finding.affected_scope || '待继续量化'}</strong></div><div className="findings-delivery-strip-item"><span>生命周期</span><strong>{lifecycleStatus(finding)}</strong></div></div>
          <div className="evidence-body"><div className="findings-compare-grid findings-delivery-grid"><div className="findings-compare-card"><span className="findings-compare-label">客户看到的问题</span><p>{impact}</p></div><div className="findings-compare-card"><span className="findings-compare-label">证据归属</span><p>{evidenceScope(finding)}</p></div><div className="findings-compare-card"><span className="findings-compare-label">回归验证要求</span><p>{closure}</p></div><div className="findings-compare-card danger"><span className="findings-compare-label">预期 vs 实际</span><p>{finding.expected_actual_comparison.difference || `${finding.expected || '未指定'} / ${finding.actual || '未捕获'}`}</p></div></div>
          {open && <><div className="findings-compare-grid"><div className="findings-compare-card"><span className="findings-compare-label">预期行为</span><p>{finding.expected || '未指定'}</p></div><div className="findings-compare-card danger"><span className="findings-compare-label">实际行为</span><p>{finding.actual || '未捕获'}</p></div></div>{regression && <div className="findings-investigation-card"><div className="findings-panel-kicker">回归验证</div><div className="findings-investigation-body"><div>生命周期：{regression.lifecycle_label}</div><div>状态：{regression.latest_status_label}</div><div>套件覆盖：{regression.included_in_suite ? `已纳入 ${regression.suite_modes.join(' / ') || '回归套件'}` : '尚未纳入回归套件'}</div><div>最近模式：{regression.last_run_mode || '未执行'}</div><div>最近时间：{regression.last_run_at || '暂无'}</div><div>说明：{regression.lifecycle_description || regression.reason || '等待后端上报回归结果。'}</div></div></div>}{regression && regression.history?.length > 0 && <div className="findings-investigation-card"><div className="findings-panel-kicker">回归历史</div><div className="findings-investigation-body">{regression.history.map((item, index) => <div key={`${item.generated_at}-${item.regression_probe_id || item.path}-${index}`}>[{item.generated_at || '未知时间'}] {item.suite_mode_label || item.suite_mode || '回归'} · {item.status_label}{item.reason ? ` · ${item.reason}` : ''}</div>)}</div></div>}{finding.evidence_chain.length > 0 && <EvidenceTimeline steps={finding.evidence_chain} />}
          {(canReplay || reproduction.steps.length > 0) && <div className={`findings-repro-grid${canReplay ? ' dual' : ''}`}>{canReplay && <div className="findings-repro-panel code"><div className="findings-command-head"><div><div className="findings-panel-kicker">接口复现</div><div className="findings-command-subtitle">{reproduction.method} {reproduction.path}</div></div><CopyButton text={replayCommand} /></div><pre className="findings-command-block"><code>{replayCommand}</code></pre><div className="findings-repro-actions"><button className="btn btn-primary btn-sm" onClick={() => setReplayFinding(finding)}>点击复现</button></div></div>}<div className="findings-repro-panel business"><div className="findings-panel-kicker">前端操作复现</div>{!reproduction.is_synthetic && reproduction.steps.length > 0 ? <ol className="findings-steps">{reproduction.steps.map((step, index) => <li key={`${step}-${index}`}>{step}</li>)}</ol> : <div className="findings-panel-note">当前未沉淀真实前端复验轨迹，请先补齐真实执行记录。</div>}</div></div>}
          {(reproduction.path || finding.source_entity || finding.evidence_hint || investigation.log_search || investigation.sql_verify) && <div className="findings-investigation-card"><div className="findings-panel-kicker warning">证据定位</div><div className="findings-investigation-body">{reproduction.path && <div>请求路径：<code>{reproduction.method} {reproduction.path}</code></div>}{finding.source_entity && <div>涉及模块：{finding.source_entity}</div>}{finding.evidence_hint && <div>证据线索：{finding.evidence_hint}</div>}{investigation.log_search && <div><code>{investigation.log_search}</code></div>}{investigation.sql_verify && <div><code>{investigation.sql_verify}</code></div>}</div></div>}
          <div className="evidence-proof"><svg viewBox="0 0 24 24" width="16" height="16"><path d="M20 6 9 17l-5-5" /></svg><div><strong>证据验证{quality.score >= 80 ? '通过' : '中'} · 复现率 {finding.proof.repro_rate}%</strong><p>{quality.label} · {quality.score}/100 · {quality.summary}</p></div></div></>}</div>
        </article>;
      })}
      {replayFinding && <Suspense fallback={<div className="replay-loading"><div className="spinner spinner-centered" /></div>}><ReplayViewer projectId={project} finding={replayFinding} onClose={() => setReplayFinding(null)} /></Suspense>}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return <button type="button" className={`btn btn-secondary btn-sm findings-copy-btn${copied ? ' copied' : ''}`} onClick={(event) => { event.stopPropagation(); void navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1500); }}>{copied ? '已复制' : '复制命令'}</button>;
}

export default Findings;
