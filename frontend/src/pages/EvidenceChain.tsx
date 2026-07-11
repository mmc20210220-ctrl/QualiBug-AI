import { lazy, Suspense, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { EvidenceTimeline } from '../components/EvidenceTimeline';
import { getKnowledgeAsset, getKnowledgePreview, evidenceArtifactUrl } from '../api/client';
import { hasCustomerFacingHardEvidence, hasRealReplayAsset, isCustomerReadyFinding, useFindingsData } from '../api/data';
import { formatActorName, formatDurationMs } from '../lib/display';
import { usePageTitle } from '../lib/page-title';
import { buildProjectPath } from '../lib/project-navigation';
import type { CommercialAssets, Finding } from '../types';

const ReplayViewer = lazy(() => import('../components/ReplayViewer'));
type EvidenceFilter = 'all' | 'API' | 'DB' | '文档';
type PersonaView = 'business' | 'test' | 'dev';
type JsonRecord = Record<string, unknown>;
type KnowledgeDocument = { source_id?: string; id?: string; display_name?: string; filename?: string; original_name?: string; type?: string; source_type?: string };

function asRecord(value: unknown): JsonRecord { return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {}; }
function asArray(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function asString(value: unknown): string { return typeof value === 'string' ? value : ''; }
function asNum(value: unknown, fallback = 0): number { const parsed = typeof value === 'number' ? value : Number(value); return Number.isFinite(parsed) ? parsed : fallback; }
function asKnowledgeDocument(value: unknown): KnowledgeDocument | null {
  const record = asRecord(value);
  if (!Object.keys(record).length) return null;
  return { source_id: asString(record.source_id) || undefined, id: asString(record.id) || undefined, display_name: asString(record.display_name) || undefined, filename: asString(record.filename) || undefined, original_name: asString(record.original_name) || undefined, type: asString(record.type) || undefined, source_type: asString(record.source_type) || undefined };
}

function moduleName(finding: Finding): string { return String(finding.business_impact.module || finding.source_entity || finding.defect_family_label || '未归类').trim() || '未归类'; }
function acceptanceHeadline(count: number, replayReady: number, clues: number): string {
  if (replayReady) return '当前可以进入客户复验。';
  if (count) return '当前已有证据包，真实复验入口尚未闭合。';
  return clues ? '当前不进入客户验收材料交付。' : '当前暂无客户验收动作。';
}

function commercialHandoffLabel(assets: CommercialAssets | null): string {
  const status = assets?.commercial_handoff.status || assets?.status || '';
  if (status === 'commercial_handoff_ready_with_validated_findings') return '商业交付已就绪';
  if (status === 'ready_for_customer_acceptance') return '待客户验收';
  if (status === 'materialized') return '交付资产已生成';
  if (status === 'empty') return '尚未生成';
  return status || '未上报';
}

function trackerSyncLabel(assets: CommercialAssets | null): string {
  const status = assets?.tracker_sync.payload_status || '';
  if (status === 'external_tracker_sync_payloads_blocked_or_empty') return '仅保留待同步草稿';
  if (status === 'external_tracker_sync_payloads_ready') return '同步载荷已就绪';
  return status || '未上报';
}

function hasDatabaseEvidence(finding: Finding): boolean {
  return finding.risk_type.includes('db') || finding.defect_family === 'data_integrity';
}

function acceptanceDecision(finding: Finding): { label: string; status: string; note: string } {
  const replayable = hasRealReplayAsset(finding);
  const ready = isCustomerReadyFinding(finding);
  if (ready && replayable) {
    return {
      label: '可进入客户复验',
      status: '可复验',
      note: '证据链与真实复验入口已闭合。',
    };
  }
  if (replayable) {
    return {
      label: '证据待闭合',
      status: '待闭合',
      note: '已有真实复验入口，但关键验收证据尚未闭合。',
    };
  }
  return {
    label: '真实复验入口未闭合',
    status: '待闭合',
    note: '当前缺少真实复验入口，不能直接进入客户验收。',
  };
}

function regressionLifecycle(finding: Finding): string {
  return asString(finding.regression?.lifecycle_label) || (finding.regression?.included_in_suite ? '待回归' : '待纳入回归');
}

function artifactTypeMeta(type: string): { icon: string; label: string } {
  const t = (type || '').toLowerCase();
  if (t.includes('screenshot') || t.includes('png') || t.includes('image') || t.includes('jpg')) return { icon: '🖼', label: '页面截图' };
  if (t.includes('har')) return { icon: '🌐', label: 'HAR 网络记录' };
  if (t.includes('trace')) return { icon: '🎬', label: '执行追踪' };
  if (t.includes('video') || t.includes('webm') || t.includes('mp4')) return { icon: '🎥', label: '执行录屏' };
  return { icon: '📎', label: type || '证据文件' };
}

function artifactName(ref: string): string {
  const parts = String(ref || '').split(/[\\/]/);
  return parts[parts.length - 1] || String(ref || '');
}

function isImageArtifact(type: string, ref: string): boolean {
  const t = `${type || ''} ${ref || ''}`.toLowerCase();
  return /screenshot|image|\.png|\.jpe?g|\.gif|\.webp|\.svg/.test(t);
}

export function EvidenceChain() {
  usePageTitle('证据链');
  const [params] = useSearchParams(); const project = params.get('project')?.trim() || '';
  const { findings, clues, rejected, commercialAssets, scanMeta, obligationProjection, loading } = useFindingsData(project);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [personaByFinding, setPersonaByFinding] = useState<Record<string, PersonaView>>({});
  const [filter, setFilter] = useState<EvidenceFilter>('all');
  const [replayFinding, setReplayFinding] = useState<Finding | null>(null);

  const customerFindings = findings.filter(isCustomerReadyFinding);
  const withEvidence = customerFindings.filter((finding) => hasCustomerFacingHardEvidence(finding) && finding.evidence_chain.length > 0);
  const currentScanDefects = asNum(scanMeta.formal_customer_deliverable_count, customerFindings.length);
  const familyShelfDefects = currentScanDefects;
  const shelfWithoutEvidence = Math.max(0, familyShelfDefects - withEvidence.length);
  const replayReady = withEvidence.filter((finding) => acceptanceDecision(finding).label === '可进入客户复验').length;
  const apiEvidence = withEvidence.filter(hasRealReplayAsset).length;
  const dbEvidence = withEvidence.filter(hasDatabaseEvidence).length;
  const documentEvidence = withEvidence.filter((finding) => !hasRealReplayAsset(finding) && !hasDatabaseEvidence(finding)).length;
  const validated = withEvidence.filter((finding) => finding.evidence_quality.level === 'validated').length;
  const oblTotal = asNum(obligationProjection.obligation_total);
  const oblCompiled = asNum(obligationProjection.obligation_compiled);
  const oblExecuted = asNum(obligationProjection.obligation_executed);
  const qualityClaim = asString(scanMeta.quality_claim_status) || 'NOT_MEASURED';
  const fingerprints = asRecord(obligationProjection.fingerprints);
  const closedLoop = Math.round((validated / Math.max(withEvidence.length, 1)) * 100);
  const display = withEvidence.filter((finding) => filter === 'all' || filter === 'API' && hasRealReplayAsset(finding) || filter === 'DB' && hasDatabaseEvidence(finding) || filter === '文档' && !hasRealReplayAsset(finding) && !hasDatabaseEvidence(finding));

  return <div>
    <section className="customer-showcase evidence-showcase mb-4">
      <div className="customer-showcase-main"><span className="panel-kicker">客户验收证据包</span><h1>{acceptanceHeadline(withEvidence.length, replayReady, clues.length)}</h1><p>{withEvidence.length ? `当前扫描确认 ${currentScanDefects} 条客户缺陷，其中 ${withEvidence.length} 条已形成客户证据包，${replayReady} 条具备真实复验入口，${validated} 条达到高质量证据标准。平台只提供缺陷事实、证据链、复验入口、客户修复后的回归结果和发布状态，不提供修复建议。` : clues.length ? `当前有 ${clues.length} 条线索仍在补采真实请求、响应、日志、数据观测或文档出处，它们不会包装成客户验收材料。` : '当前项目尚未形成客户可验收的证据包。'}</p><div className="page-summary-strip"><span className="summary-pill strong">可交付 {currentScanDefects}</span><span className="summary-pill">候选 {clues.length}</span><span className="summary-pill">已拒绝 {rejected.length}</span><span className="summary-pill">可验收证据包 {withEvidence.length}</span><span className="summary-pill">真实复验入口 {replayReady}</span><span className="summary-pill">闭环率 {closedLoop}%</span>{commercialAssets && <span className="summary-pill">商业交付 {commercialHandoffLabel(commercialAssets)}</span>}</div>{(oblTotal > 0 || qualityClaim === 'NOT_MEASURED') && <div className="page-summary-strip" style={{ marginTop: 8 }}>{oblTotal > 0 && <span className="summary-pill">义务 {oblTotal}</span>}{oblTotal > 0 && <span className="summary-pill">已编译 {oblCompiled}</span>}{oblTotal > 0 && <span className="summary-pill">已执行 {oblExecuted}</span>}<span className="summary-pill">{qualityClaim === 'MEASURED' ? '外部质量已评测' : '尚未完成外部质量评测'}</span>{Boolean(fingerprints.source_hash) && <span className="summary-pill">source {String(fingerprints.source_hash).slice(0, 8)}</span>}</div>}<div className="customer-showcase-actions"><Link className="btn btn-primary" to={buildProjectPath('/findings', project)}>查看缺陷闭环清单</Link>{clues.length > 0 && <Link className="btn btn-secondary" to={buildProjectPath('/clues', project)}>查看内部线索</Link>}<Link className="btn btn-secondary" to={buildProjectPath('/materials', project)}>查看企业资料</Link></div></div>
      <div className="customer-showcase-side"><div className={`customer-status-card ${replayReady ? 'success' : withEvidence.length ? 'warning' : 'danger'}`}><span>验收状态</span><strong>{replayReady ? '可进入客户复验' : withEvidence.length ? '复验入口未闭合' : '当前不交付验收'}</strong><p>{replayReady ? `${replayReady} 条证据包已具备真实客户复验入口。` : '没有完整真实证据时，不能把候选或模拟结果作为客户缺陷交付。'}</p></div><div className="customer-status-card neutral"><span>责任边界</span><strong>不提供修复建议</strong><p>QualiBug-AI 只确认问题是否存在、客户修复后是否回归通过，以及当前发布/交付状态。</p></div><div className="customer-status-meta"><span><em>API 证据</em><b>{apiEvidence}</b></span><span><em>数据证据</em><b>{dbEvidence}</b></span><span><em>文档证据</em><b>{documentEvidence}</b></span>{commercialAssets && <><span><em>商业交付</em><b>{commercialHandoffLabel(commercialAssets)}</b></span><span><em>Tracker 同步</em><b>{trackerSyncLabel(commercialAssets)}</b></span></>}</div></div>
    </section>

    {commercialAssets && <div className="customer-summary-grid evidence-summary-grid mb-4">{[
      { label: '商业交付状态', value: commercialHandoffLabel(commercialAssets), tone: commercialAssets.status === 'materialized' ? 'success' : 'warning', note: commercialAssets.commercial_handoff.acceptance_status || '等待商业交付状态' },
      { label: '交付包', value: commercialAssets.delivery_package.status === 'created' ? '已创建' : '未创建', tone: commercialAssets.delivery_package.status === 'created' ? 'success' : 'warning', note: commercialAssets.delivery_package.package_ref || '尚未生成交付包路径' },
      { label: 'Tracker 同步', value: trackerSyncLabel(commercialAssets), tone: commercialAssets.tracker_sync.payload_status ? 'neutral' : 'warning', note: commercialAssets.tracker_sync.payload_gate_status || '等待同步 Gate' },
      { label: '客户复验资产', value: commercialAssets.customer_ready_reproduction_count, tone: commercialAssets.customer_ready_reproduction_count > 0 ? 'primary' : 'warning', note: `覆盖 ${commercialAssets.finding_count} 条商业交付 finding` },
    ].map((item) => <article key={item.label} className={`customer-summary-card tone-${item.tone}`}><span>{item.label}</span><strong>{item.value}</strong><small>{item.note}</small></article>)}</div>}

    {withEvidence.length > 0 && <><div className="customer-summary-grid evidence-summary-grid mb-4">{[
      { label: '本轮确认缺陷', value: currentScanDefects, tone: 'primary', note: '以当前 scan_meta.customer_ready_defects 为准' },
      { label: '累计缺陷货架', value: familyShelfDefects, tone: familyShelfDefects > currentScanDefects ? 'warning' : 'neutral', note: familyShelfDefects > currentScanDefects ? `仍有 ${shelfWithoutEvidence} 条来自历史货架或待补强证据包` : '当前与本轮结果一致' },
      { label: '可验收证据包', value: withEvidence.length, tone: 'primary', note: '仅统计真实运行证据完整的缺陷' },
      { label: '真实复验入口', value: replayReady, tone: replayReady ? 'success' : 'warning', note: replayReady ? '可按真实入口复验' : '真实复验入口尚未闭合' },
      { label: '高质量证据', value: validated, tone: 'success', note: '原始证据与复验入口完整' },
      { label: '证据闭环率', value: `${closedLoop}%`, tone: 'warning', note: '证据越完整，验收口径越清晰' },
    ].map((item) => <article key={item.label} className={`customer-summary-card tone-${item.tone}`}><span>{item.label}</span><strong>{item.value}</strong><small>{item.note}</small></article>)}</div>
    <div className="filters behavior-filters evidence-filter-bar mb-4">{([{ label: `全部 (${withEvidence.length})`, value: 'all' }, { label: `API (${apiEvidence})`, value: 'API' }, { label: `DB (${dbEvidence})`, value: 'DB' }, { label: `文档 (${documentEvidence})`, value: '文档' }] as Array<{ label: string; value: EvidenceFilter }>).map((item) => <button key={item.value} onClick={() => setFilter(item.value)} className={`filter${filter === item.value ? ' active' : ''}`}>{item.label}</button>)}</div></>}

    {loading && <section className="findings-empty-state compact"><div className="spinner spinner-centered" /><p>正在整理证据链...</p></section>}
    {!loading && display.length === 0 && <section className="findings-empty-state compact"><span className="findings-empty-kicker">当前结论</span><h3>{filter === 'all' ? '当前没有可交付证据包' : `无 ${filter} 类型可交付证据链`}</h3><p>{clues.length ? `当前仅有 ${clues.length} 条待验证线索，尚未形成客户可验收的证据闭环。` : '运行扫描并形成已验证缺陷后，这里会自动展示真实可交付的证据链。'}</p>{clues.length > 0 && <Link className="btn btn-secondary" to={buildProjectPath('/clues', project)}>查看待验证线索</Link>}</section>}

    {display.map((finding) => {
      const open = expandedId === finding.id;
      const persona = personaByFinding[finding.id] || 'business';
      const quality = finding.evidence_quality; const reproduction = finding.reproduction; const business = finding.business_impact; const investigation = finding.investigation_guidance;
      const replayable = hasRealReplayAsset(finding); const command = replayable ? quality.curl_command : '';
      const decision = acceptanceDecision(finding);
      const chain = persona === 'business' ? finding.evidence_chain_business || finding.evidence_chain : persona === 'test' ? finding.evidence_chain_test || finding.evidence_chain : finding.evidence_chain_dev || finding.evidence_chain;
      return <article key={finding.id} className={`evidence-delivery-card severity-${finding.severity.toLowerCase()} evidence-quality-${quality.level}${open ? ' open' : ''}`}>
        <div className="evidence-delivery-head" onClick={() => setExpandedId(open ? null : finding.id)}><div className="evidence-delivery-title"><div className="evidence-delivery-badges"><span className={`severity ${finding.severity.toLowerCase()}`}>{finding.severity}</span><span className={`bug-status-badge bug-status-${finding.bug_status}`}>{finding.bug_status_label}</span><span className="evidence-delivery-badge subtle">{moduleName(finding)}</span><span className="evidence-delivery-badge">{regressionLifecycle(finding)}</span></div><h2>{finding.title}</h2><p>{quality.summary || business.summary || finding.business_summary}</p></div><div className="evidence-delivery-meta"><span><em>验收结论</em><b>{decision.label}</b></span><span><em>证据评分</em><b>{quality.score}/100</b></span><span><em>复验入口</em><b>{replayable ? `${reproduction.method} ${reproduction.path}` : '待补充真实复验入口'}</b></span><button type="button" className="btn btn-secondary btn-sm" onClick={(event) => { event.stopPropagation(); setExpandedId(open ? null : finding.id); }}>{open ? '收起细节' : '查看细节'}</button></div></div>
        <div className="evidence-delivery-strip"><div className="evidence-delivery-strip-item"><span>验收判断</span><strong>{decision.status}</strong></div><div className="evidence-delivery-strip-item"><span>影响范围</span><strong>{finding.affected_scope || `${moduleName(finding)}相关流程`}</strong></div><div className="evidence-delivery-strip-item"><span>回归闭环状态</span><strong>{finding.regression?.latest_status_label || decision.note}</strong></div><div className="evidence-delivery-strip-item"><span>生命周期</span><strong>{regressionLifecycle(finding)}</strong></div></div>
        <div className="evidence-body"><div className="evidence-acceptance-grid"><div className="findings-compare-card"><span className="findings-compare-label">客户验收看到的结论</span><p>{business.summary || finding.business_summary || finding.actual}</p></div><div className="findings-compare-card"><span className="findings-compare-label">复验动作</span><p>{replayable ? `${reproduction.method} ${reproduction.path}` : '当前未沉淀真实复验入口，不能进入客户复验。'}</p></div><div className="findings-compare-card"><span className="findings-compare-label">预期 vs 实际</span><p>{finding.expected_actual_comparison.difference || finding.actual}</p></div><div className="findings-compare-card danger"><span className="findings-compare-label">验收缺口</span><p>{quality.missing.length ? quality.missing.join('；') : '当前未发现显著验收缺口。'}</p></div></div>
        {open && <><div className={`evidence-dossier ${quality.level}`}><div className="evidence-dossier-head"><div><span className="panel-kicker">证据闭环概要</span><strong>{decision.label} · {quality.score}/100</strong></div><span className="evidence-dossier-status">{decision.status}</span></div>{chain.length > 0 && <EvidenceTimeline steps={chain} />}<div className="evidence-dossier-proof"><span><em>复现入口</em><strong>{replayable ? `${reproduction.method} ${reproduction.path}` : '待补充真实复现'}</strong></span><span><em>预期规则</em><strong>{finding.expected ? '已记录' : '待关联'}</strong></span><span><em>实际结果</em><strong>{finding.actual ? '已记录' : '待采集'}</strong></span><span><em>验收缺口</em><strong>{quality.missing.length}</strong></span></div></div>
        {finding.regression && <div className="findings-investigation-card"><div className="findings-panel-kicker">回归闭环</div><div className="findings-investigation-body"><div>生命周期：{finding.regression.lifecycle_label}</div><div>最新状态：{finding.regression.latest_status_label}</div><div>说明：{finding.regression.lifecycle_description || finding.regression.reason || '等待回归结果。'}</div>{finding.regression.history?.length > 0 && <div>最近轨迹：{finding.regression.history.map((item) => `[${item.generated_at || '未知时间'}] ${item.status_label}`).join(' -> ')}</div>}</div></div>}
        <div className="persona-tabs">{([{ key: 'business', label: '业务视角' }, { key: 'test', label: '测试视角' }, { key: 'dev', label: '技术证据' }] as Array<{ key: PersonaView; label: string }>).map((tab) => <button key={tab.key} onClick={(event) => { event.stopPropagation(); setPersonaByFinding((previous) => ({ ...previous, [finding.id]: tab.key })); }} className={`persona-tab${persona === tab.key ? ' active' : ''}`}>{tab.label}</button>)}</div>
        {persona === 'business' && <div className="persona-panel"><div className="persona-section hero"><div className="persona-kicker">业务影响</div><p className="persona-summary">{business.summary || finding.actual}</p><div className="persona-inline-meta"><span><em>影响模块</em><strong>{business.module || moduleName(finding)}</strong></span><span><em>紧急程度</em><strong>{business.urgency || finding.severity}</strong></span></div></div><div className="persona-section warning"><div className="persona-kicker">需求来源</div>{finding.doc_refs.length ? finding.doc_refs.slice(0, 3).map((document, index) => <div key={`${document.source_id || document.display_name || 'document'}-${index}`} className="persona-doc-card"><span>{document.display_name || '文档'}</span>{document.excerpt && <div className="persona-doc-excerpt">{document.excerpt}</div>}</div>) : <p>尚未关联资料出处。</p>}</div></div>}
        {persona === 'test' && <div className="persona-panel"><div className="persona-section success"><div className="persona-kicker">复现状态</div><div className="repro-status-bar"><span className={`repro-status-badge ${finding.is_reproducible ? 'reproducible' : 'not-reproducible'}`}>{finding.is_reproducible ? '✓ 可稳定复现' : '✗ 未通过复现门控'}</span><span className="repro-confidence">复现置信度: {Math.round(finding.confidence * 100)}%</span></div>{reproduction.steps.length ? <ol className="persona-step-list">{reproduction.steps.map((step, index) => <li key={`${step}-${index}`}><code>{step}</code></li>)}</ol> : <p>当前没有真实复现步骤。</p>}</div>{replayable && <button className="btn btn-primary btn-sm" onClick={() => setReplayFinding(finding)}>点击复现</button>}</div>}
        {persona === 'dev' && <div className="persona-panel"><div className="persona-section neutral"><div className="persona-kicker">技术定位</div><div className="dev-technical-details"><div className="dev-tech-row"><span><em>涉及接口</em><code>{finding.technical_details.api_endpoint.method} {finding.technical_details.api_endpoint.path}</code></span>{finding.technical_details.api_endpoint.actor && <span><em>操作者</em><code>{formatActorName(finding.technical_details.api_endpoint.actor)}</code></span>}</div>{finding.technical_details.response_status > 0 && <div className="dev-tech-row"><span><em>响应状态码</em><code>{finding.technical_details.response_status}</code></span><span><em>耗时</em><code>{formatDurationMs(finding.expected_actual_comparison.api_comparison?.duration_ms)}</code></span></div>}</div></div><div className="dev-debug-panel"><div className="persona-kicker dark">定位材料</div>{[{ label: '定位线索', value: investigation.primary_area || finding.source_entity || finding.title }, { label: '复现命令', value: command || '缺少真实复现资产，不能生成命令。' }, { label: '日志定位条件', value: investigation.log_search }, { label: '数据核验语句', value: investigation.sql_verify }].filter((item) => item.value).map((item) => <div key={item.label} className="dev-debug-item"><span>{item.label}</span><code>{item.value}</code></div>)}</div>{finding.raw_evidence.has_real_evidence && <div className="raw-evidence-panel"><div className="raw-evidence-item"><span className="raw-evidence-label">请求</span><code>{finding.raw_evidence.request_raw.method} {finding.raw_evidence.request_raw.path}</code></div><div className="raw-evidence-item"><span className="raw-evidence-label">响应</span><code>HTTP {finding.raw_evidence.response_raw.status_code} · {formatDurationMs(finding.raw_evidence.response_raw.duration_ms)}</code></div></div>}{(finding.raw_evidence.ui_artifacts?.length ?? 0) > 0 && <div className="raw-evidence-panel ui-artifacts-panel"><div className="raw-evidence-item"><span className="raw-evidence-label">视觉证据 ({finding.raw_evidence.ui_artifacts!.length})</span><div className="ui-artifact-grid">{finding.raw_evidence.ui_artifacts!.map((artifact, index) => { const meta = artifactTypeMeta(artifact.type); const url = evidenceArtifactUrl(project, artifact.ref); const isImage = isImageArtifact(artifact.type, artifact.ref); return <div key={`${artifact.ref}-${index}`} className={`ui-artifact-card ui-artifact-${(artifact.type || 'file').toLowerCase()}${isImage ? ' has-thumb' : ''}`}>{isImage ? <a href={url} target="_blank" rel="noopener noreferrer" className="ui-artifact-thumb"><img src={url} alt={meta.label} loading="lazy" /></a> : <span className="ui-artifact-icon" aria-hidden="true">{meta.icon}</span>}<div className="ui-artifact-info"><strong>{meta.label}</strong><a href={url} target="_blank" rel="noopener noreferrer" title={artifact.ref} className="ui-artifact-link">{artifactName(artifact.ref)}</a></div></div>; })}</div></div></div>}</div>}
        </>}</div>
      </article>;
    })}
    <EnterpriseDocuments project={project} />
    {replayFinding && <Suspense fallback={<div className="replay-loading"><div className="spinner spinner-centered" /></div>}><ReplayViewer projectId={project} finding={replayFinding} onClose={() => setReplayFinding(null)} /></Suspense>}
  </div>;
}

function EnterpriseDocuments({ project }: { project: string }) {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]); const [loading, setLoading] = useState(false); const [previewId, setPreviewId] = useState<string | null>(null); const [preview, setPreview] = useState('');
  useEffect(() => {
    if (!project) { setDocuments([]); return; }
    setLoading(true);
    getKnowledgeAsset(project).then((value) => {
      const payload = asRecord(value); const asset = asRecord(payload.knowledge_asset); const sources = asArray(asset.sources || payload.source_inventory);
      setDocuments(sources.map(asKnowledgeDocument).filter((item): item is KnowledgeDocument => item !== null));
    }).catch(() => setDocuments([])).finally(() => setLoading(false));
  }, [project]);
  const loadPreview = async (sourceId: string): Promise<void> => {
    if (previewId === sourceId) { setPreviewId(null); setPreview(''); return; }
    setPreviewId(sourceId);
    try { setPreview(asString(asRecord(await getKnowledgePreview(sourceId)).content) || '无法加载文档内容'); }
    catch { setPreview('加载失败'); }
  };
  if (!project || !documents.length && !loading) return null;
  return <div className="evidence-docs-panel"><div className="evidence-docs-head"><span>企业资料 ({documents.length})</span><Link to={buildProjectPath('/materials', project)}>前往管理</Link></div>{loading ? <div className="evidence-docs-loading">加载中...</div> : <div className="evidence-docs-list">{documents.map((document) => { const id = document.source_id || document.id || ''; return <div key={id || document.filename || document.display_name}><div onClick={() => { if (id) void loadPreview(id); }} className={`evidence-doc-row${previewId === id ? ' active' : ''}${id ? ' clickable' : ''}`}><span>{document.display_name || document.filename || document.original_name || document.source_id || '未命名文档'}</span><span className="evidence-doc-type">{document.type || document.source_type || '文档'}</span></div>{previewId === id && <div className="evidence-doc-preview">{preview || '加载中...'}</div>}</div>; })}</div>}</div>;
}

export default EvidenceChain;
