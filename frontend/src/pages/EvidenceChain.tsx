import { lazy, Suspense, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { EvidenceTimeline } from '../components/EvidenceTimeline';
import { getKnowledgeAsset, getKnowledgePreview } from '../api/client';
import { hasCustomerFacingHardEvidence, hasRealReplayAsset, isCustomerReadyFinding, useFindingsData } from '../api/data';
import { formatActorName, formatDurationMs } from '../lib/display';
import { usePageTitle } from '../lib/page-title';
import { formatBeijingDateTime } from '../lib/time';
import { buildProjectPath } from '../lib/project-navigation';
import type { Finding } from '../types';

const ReplayViewer = lazy(() => import('../components/ReplayViewer'));

type EvidenceFilter = 'all' | 'API' | 'DB' | '文档';
type PersonaView = 'business' | 'test' | 'dev';
type KnowledgeDocument = {
  source_id?: string;
  id?: string;
  display_name?: string;
  filename?: string;
  original_name?: string;
  type?: string;
  source_type?: string;
  stored_path?: string;
};

function businessUrgencyLabel(urgency: string | undefined, severity: string) {
  const normalized = String(urgency || '').trim();
  if (normalized) return normalized;
  return severity === 'P0' ? '高' : severity === 'P1' ? '中高' : '中';
}

function getFindingModule(finding: Finding) {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '核心业务').trim() || '核心业务';
}

function getAcceptanceHeadline(count: number, replayReadyCount: number, hiddenPendingCount: number) {
  if (replayReadyCount > 0) return '当前可以进入客户复验。';
  if (count > 0) return '当前已有证据包，但仍未形成真实客户复验入口。';
  if (hiddenPendingCount > 0) return '当前不建议向客户交付验收材料。';
  return '当前暂无客户验收动作。';
}

function getEvidenceSourceLabel(finding: Finding) {
  const hasApi = Boolean(finding.reproduction?.path || finding.repro_path);
  const hasDb = Boolean(finding.risk_type?.includes('db') || finding.defect_family === 'data_integrity');
  if (hasApi && hasDb) return '接口 + 数据库证据';
  if (hasApi) return '接口/回放证据';
  if (hasDb) return '数据库证据';
  return '文档/规则证据';
}

export function EvidenceChain() {
  usePageTitle('证据链');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { findings, clues, loading } = useFindingsData(project);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [activePersona, setActivePersona] = useState<Record<string, PersonaView>>({});
  const [filter, setFilter] = useState<EvidenceFilter>('all');
  const [replayFinding, setReplayFinding] = useState<Finding | null>(null);

  const customerReadyFindings = findings.filter(isCustomerReadyFinding);
  const hiddenPendingCount = clues.length;
  const withEvidence = customerReadyFindings.filter((f) => hasCustomerFacingHardEvidence(f) && (f.evidence_chain?.length || 0) >= 1);
  const apiEvidence = withEvidence.filter((f) => hasRealReplayAsset(f)).length;
  const dbEvidence = withEvidence.filter((f) => f.risk_type?.includes('db') || f.defect_family === 'data_integrity').length;
  const docEvidence = withEvidence.filter((f) => !hasRealReplayAsset(f) && !f.risk_type?.includes('db') && f.defect_family !== 'data_integrity').length;
  const validatedEvidence = withEvidence.filter((f) => f.evidence_quality?.level === 'validated').length;
  const replayReadyCount = withEvidence.filter((f) => hasRealReplayAsset(f)).length;
  const evidenceClosedLoop = Math.round((validatedEvidence / Math.max(withEvidence.length, 1)) * 100);
  const topModules = Array.from(new Set(withEvidence.map(getFindingModule))).slice(0, 3);

  const displayData = (() => {
    if (filter === 'all') return withEvidence;
    if (filter === 'API') return withEvidence.filter((f) => hasRealReplayAsset(f));
    if (filter === 'DB') return withEvidence.filter((f) => f.risk_type?.includes('db') || f.defect_family === 'data_integrity');
    if (filter === '文档') {
      return withEvidence.filter((f) => !hasRealReplayAsset(f) && !f.risk_type?.includes('db') && f.defect_family !== 'data_integrity');
    }
    return withEvidence;
  })();

  const persona = (fid: string): PersonaView => activePersona[fid] || 'business';
  const personaTabs: { key: PersonaView; label: string; desc: string }[] = [
    { key: 'business', label: '业务视角', desc: '产品与业务负责人' },
    { key: 'test', label: '测试视角', desc: '测试与验收复核' },
    { key: 'dev', label: '研发视角', desc: '研发定位与复盘' },
  ];

  return (
    <div>
      <section className="customer-showcase evidence-showcase mb-4">
        <div className="customer-showcase-main">
          <span className="panel-kicker">客户验收证据包</span>
          <h1>{getAcceptanceHeadline(withEvidence.length, replayReadyCount, hiddenPendingCount)}</h1>
          <p>
            {withEvidence.length > 0
              ? `当前共有 ${withEvidence.length} 条客户证据包，其中 ${replayReadyCount} 条已形成真实复验入口，${validatedEvidence} 条达到高质量证据标准。重点涉及 ${topModules.length > 0 ? topModules.join('、') : '核心业务'} 等模块。`
              : hiddenPendingCount > 0
                ? `当前只有 ${hiddenPendingCount} 条线索仍在补采真实请求、日志、DB 快照或文档出处，它们不会包装成客户验收材料。`
                : '当前项目尚未形成客户可验收的证据包，后续出现 confirmed 缺陷后这里会自动更新。'}
          </p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">
              {validatedEvidence > 0 ? `证据强度 高 (${validatedEvidence}/${withEvidence.length})` : withEvidence.length > 0 ? `证据强度 待增强 (${validatedEvidence}/${withEvidence.length})` : '证据强度 暂无'}
            </span>
            <span className="summary-pill">可验收证据包 {withEvidence.length}</span>
            <span className="summary-pill">真实复验入口 {replayReadyCount}</span>
            <span className="summary-pill">闭环率 {evidenceClosedLoop}%</span>
            <span className="summary-pill">待验证线索 {hiddenPendingCount}</span>
          </div>
          <div className="customer-showcase-actions">
            <Link className="btn btn-primary" to={buildProjectPath('/findings', project)}>查看整改清单</Link>
            {hiddenPendingCount > 0 && <Link className="btn btn-secondary" to={buildProjectPath('/clues', project)}>查看内部线索</Link>}
            <Link className="btn btn-secondary" to={buildProjectPath('/materials', project)}>查看企业资料</Link>
          </div>
        </div>
        <div className="customer-showcase-side">
          <div className={`customer-status-card ${replayReadyCount > 0 ? 'success' : withEvidence.length > 0 ? 'warning' : 'danger'}`}>
            <span>验收建议</span>
            <strong>
              {replayReadyCount > 0
                ? '可进入客户复验'
                : withEvidence.length > 0
                  ? '先补齐真实复验入口'
                  : '当前不建议交付验收'}
            </strong>
            <p>
              {replayReadyCount > 0
                ? `${replayReadyCount} 条证据包已具备真实客户复验入口。`
                : withEvidence.length > 0
                  ? `当前已有证据包，但仍缺少客户可执行的真实复验入口，不应把建议性指引当作复验资产。`
                  : '没有 confirmed 证据包时，不应向客户展示不完整的验收材料。'}
            </p>
          </div>
          <div className="customer-status-meta">
            <span><em>API 证据</em><b>{apiEvidence}</b></span>
            <span><em>数据证据</em><b>{dbEvidence}</b></span>
            <span><em>文档证据</em><b>{docEvidence}</b></span>
          </div>
        </div>
      </section>

      {withEvidence.length > 0 && (
        <div className="customer-summary-grid evidence-summary-grid mb-4">
          {[
            { label: '可验收证据包', val: withEvidence.length, tone: 'primary', note: '仅统计具备真实原始证据的缺陷' },
            { label: '真实复验入口', val: replayReadyCount, tone: replayReadyCount > 0 ? 'success' : 'warning', note: replayReadyCount > 0 ? '客户已可按真实入口复验' : '当前仍需补齐真实复验入口' },
            { label: '高质量证据', val: validatedEvidence, tone: 'success', note: '原始证据与复验入口完整' },
            { label: '证据闭环率', val: `${evidenceClosedLoop}%`, tone: 'warning', note: '证据越完整，客户越容易验收' },
          ].map((item) => (
            <article key={item.label} className={`customer-summary-card tone-${item.tone}`}>
              <span>{item.label}</span>
              <strong>{item.val}</strong>
              <small>{item.note}</small>
            </article>
          ))}
        </div>
      )}

      {withEvidence.length > 0 && (
        <section className="customer-value-grid evidence-agenda-grid mb-4">
          <article className="customer-value-card">
            <span className="customer-value-kicker">本页价值</span>
            <h2>客户先看“为什么这个问题站得住”</h2>
            <p>每个证据包先呈现验收结论、证据强度和复验动作，技术细节与多角色视图全部下沉到展开层。</p>
          </article>
          <article className="customer-value-card">
            <span className="customer-value-kicker">验收边界</span>
            <h2>只展示已形成真实证据的缺陷</h2>
            <p>待验证线索仍保留在内部线索池，不混入客户验收页，避免让客户把线索误认为已确认问题。</p>
          </article>
          <article className="customer-value-card">
            <span className="customer-value-kicker">验收动作</span>
            <h2>每条证据包都明确标注是否已有真实复验入口</h2>
            <p>客户和测试负责人可以直接区分“已可复验”和“仍需补证”，研发也能在展开后继续定位。</p>
          </article>
        </section>
      )}

      {withEvidence.length > 0 && (
        <div className="filters behavior-filters evidence-filter-bar mb-4">
          {([
            { label: `全部 (${withEvidence.length})`, value: 'all' as EvidenceFilter },
            { label: `API (${apiEvidence})`, value: 'API' as EvidenceFilter },
            { label: `DB (${dbEvidence})`, value: 'DB' as EvidenceFilter },
            { label: `文档 (${docEvidence})`, value: '文档' as EvidenceFilter },
          ]).map((item) => (
            <button key={item.value} onClick={() => setFilter(item.value)} className={`filter${filter === item.value ? ' active' : ''}`}>
              {item.label}
            </button>
          ))}
        </div>
      )}

      {loading && <section className="findings-empty-state compact"><div className="spinner spinner-centered" /><p>正在整理证据链...</p></section>}

      {!loading && displayData.length === 0 && (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">当前结论</span>
          <h3>{filter !== 'all' ? `无 ${filter} 类型可交付证据链` : '当前没有可交付证据包'}</h3>
          <p>{hiddenPendingCount > 0 ? `当前仅检测到 ${hiddenPendingCount} 条待验证线索，尚未形成客户可验收的证据闭环。请继续补采真实请求、日志、DB 快照或文档出处。` : '运行扫描并形成已验证缺陷后，这里会自动展示真实可交付的证据链。'}</p>
          {hiddenPendingCount > 0 && <Link className="btn btn-secondary" to={buildProjectPath('/clues', project)}>查看待验证线索</Link>}
        </section>
      )}

      {!loading && displayData.length > 0 && (
        <div className="page-header evidence-page-header">
          <div>
            <span className="panel-kicker">验收清单</span>
            <h1>证据链</h1>
            <p>按证据来源和验收强度浏览每条缺陷的证据包，首屏直接给出验收判断和复验入口。</p>
          </div>
          <div className="findings-toolbar-note">
            当前展示 {filter === 'all' ? '全部' : filter} 证据包
          </div>
        </div>
      )}

      {displayData.map((f) => {
        const isOpen = expandedId === f.id;
        const pv = persona(f.id);
        const quality = f.evidence_quality || { level: 'needs_evidence', score: 0, label: '', summary: '', verified: [], missing: [], next_actions: [], can_reproduce: false, curl_command: '' };
        const repro = f.reproduction || { method: '', path: '', steps: [], curl_command: '', is_synthetic: false };
        const inv = f.investigation_guidance || { primary_area: '', relevant_apis: [], relevant_tables: [], log_search: '', sql_verify: '', trace_id: '' };
        const biz = f.business_impact || { summary: '', urgency: '', module: '' };
        const realReproEntry = hasRealReplayAsset(f);
        const debugCurlCommand = realReproEntry ? quality.curl_command : '';
        const moduleName = getFindingModule(f);
        const acceptanceAction = quality.next_actions?.[0] || f.recommended_fix || '按复验入口完成验证，并补齐缺失证据。';
        const acceptanceSummary = quality.summary || f.business_summary || biz.summary || '该问题已具备客户验收所需的基础证据。';
        const affectedScope = f.affected_scope || `${moduleName}相关流程`;

        return (
          <article key={f.id} className={`evidence-delivery-card severity-${f.severity.toLowerCase()} evidence-quality-${quality.level}${isOpen ? ' open' : ''}`}>
            <div className="evidence-delivery-head" onClick={() => setExpandedId(isOpen ? null : f.id)}>
              <div className="evidence-delivery-title">
                <div className="evidence-delivery-badges">
                  <span className={`severity ${f.severity.toLowerCase()}`}>{f.severity}</span>
                  <span className={`bug-status-badge bug-status-${f.bug_status || 'risk_clue'}`}>{f.bug_status_label || '风险线索'}</span>
                  <span className="evidence-delivery-badge subtle">{moduleName}</span>
                </div>
                <h2>{f.title}</h2>
                <p>{acceptanceSummary}</p>
              </div>
              <div className="evidence-delivery-meta">
                <span><em>验收结论</em><b>{quality.label || '待补证'}</b></span>
                <span><em>证据评分</em><b>{quality.score}/100</b></span>
                <span><em>复验入口</em><b>{realReproEntry ? `${repro.method} ${repro.path}` : '待补充真实复验入口'}</b></span>
                <button type="button" className="btn btn-secondary btn-sm" onClick={(e) => { e.stopPropagation(); setExpandedId(isOpen ? null : f.id); }}>
                  {isOpen ? '收起细节' : '查看细节'}
                </button>
              </div>
            </div>

            <div className="evidence-delivery-strip">
              <div className="evidence-delivery-strip-item">
                <span>验收判断</span>
                <strong>{realReproEntry ? '可直接复验' : '需先补齐复验条件'}</strong>
              </div>
              <div className="evidence-delivery-strip-item">
                <span>影响范围</span>
                <strong>{affectedScope}</strong>
              </div>
              <div className="evidence-delivery-strip-item">
                <span>下一步动作</span>
                <strong>{acceptanceAction}</strong>
              </div>
              <div className="evidence-delivery-strip-item">
                <span>证据来源</span>
                <strong>{getEvidenceSourceLabel(f)}</strong>
              </div>
            </div>

            <div className="evidence-body">
              <div className="evidence-acceptance-grid">
                <div className="findings-compare-card">
                  <span className="findings-compare-label">客户验收看到的结论</span>
                  <p>{biz.summary || f.business_summary || f.actual || '该问题已形成可交付证据包。'}</p>
                </div>
                <div className="findings-compare-card">
                  <span className="findings-compare-label">复验动作</span>
                  <p>{realReproEntry ? `${repro.method} ${repro.path}` : '当前未沉淀真实复验入口，建议先补齐。'}</p>
                </div>
                <div className="findings-compare-card">
                  <span className="findings-compare-label">业务影响</span>
                  <p>{f.expected_actual_comparison?.difference || biz.summary || '该问题对业务流程存在可观测影响。'}</p>
                </div>
                <div className="findings-compare-card danger">
                  <span className="findings-compare-label">验收缺口</span>
                  <p>{quality.missing?.length ? quality.missing.join('；') : '当前未发现显著验收缺口。'}</p>
                </div>
              </div>

              {isOpen && (
                <>
                  <div className={`evidence-dossier ${quality.level}`}>
                    <div className="evidence-dossier-head">
                      <div>
                        <span className="panel-kicker">证据闭环概要</span>
                        <strong>{quality.label} · {quality.score}/100</strong>
                      </div>
                      <span className="evidence-dossier-status">{realReproEntry ? '可复验' : '待补证'}</span>
                    </div>
                    {(() => {
                      const personaChain = pv === 'business' ? (f.evidence_chain_business || f.evidence_chain)
                        : pv === 'test' ? (f.evidence_chain_test || f.evidence_chain)
                        : (f.evidence_chain_dev || f.evidence_chain);
                      return personaChain && personaChain.length > 0 ? <EvidenceTimeline steps={personaChain} /> : null;
                    })()}
                    {f.evidence_completeness && (
                      <div className="evidence-completeness-bar">
                        <div className="evidence-completeness-head">
                          <span>证据完备度</span>
                          <strong>{f.evidence_completeness.score}%（{f.evidence_completeness.present_count}/{f.evidence_completeness.total}）</strong>
                        </div>
                        <div className="evidence-completeness-progress">
                          <div className="evidence-completeness-fill" style={{ width: `${f.evidence_completeness.score}%` }} />
                        </div>
                        <div className="evidence-completeness-dims">
                          {f.evidence_completeness.dimensions.map((d) => (
                            <span key={d.key} className={`evidence-completeness-dim ${d.present ? 'present' : 'missing'}`}>
                              <span className="evidence-completeness-icon">{d.present ? '✓' : '○'}</span>
                              {d.label}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="evidence-dossier-proof">
                      <span><em>复现入口</em><strong>{realReproEntry ? `${repro.method} ${repro.path}` : '待补充真实复现'}</strong></span>
                      <span><em>预期规则</em><strong>{f.expected ? '已记录' : '待关联'}</strong></span>
                      <span><em>实际结果</em><strong>{f.actual ? '已记录' : '待采集'}</strong></span>
                      <span><em>验收缺口</em><strong>{quality.missing?.length || 0}</strong></span>
                    </div>
                  </div>

                  <div className="persona-tabs">
                    {personaTabs.map((tab) => (
                      <button
                        key={tab.key}
                        onClick={(e) => {
                          e.stopPropagation();
                          setActivePersona((prev) => ({ ...prev, [f.id]: tab.key }));
                        }}
                        className={`persona-tab${pv === tab.key ? ' active' : ''}`}
                      >
                        {tab.label}
                        <span>{tab.desc}</span>
                      </button>
                    ))}
                  </div>

                  {pv === 'business' && (
                    <div className="persona-panel">
                      <div className={`evidence-quality-card ${quality.level}`}>
                        <div>
                          <span className="evidence-quality-label">{quality.label}</span>
                          <strong>{quality.score}/100</strong>
                        </div>
                        <p>{quality.summary}</p>
                      </div>
                      <div className="persona-section hero">
                        <div className="persona-kicker">业务影响</div>
                        <p className="persona-summary">{biz.summary || f.actual || '该缺陷可能导致业务流程异常，影响用户体验和业务数据一致性。'}</p>
                        <div className="persona-inline-meta">
                          <span><em>影响模块</em><strong>{biz.module || f.source_entity || '核心业务'}</strong></span>
                          <span><em>紧急程度</em><strong>{businessUrgencyLabel(biz.urgency, f.severity)}</strong></span>
                          <span><em>影响范围</em><strong>{f.affected_scope || '核心业务'}</strong></span>
                        </div>
                      </div>
                      <div className="persona-section neutral">
                        <div className="persona-kicker">业务描述</div>
                        <p>{f.expected || '系统在特定操作序列下未按预期业务规则响应，存在状态不一致风险。'}</p>
                      </div>
                      <div className="persona-section warning">
                        <div className="persona-kicker">需求来源</div>
                        {f.doc_refs?.length ? (
                          f.doc_refs.slice(0, 3).map((doc, i) => (
                            <div key={i} className="persona-doc-card">
                              <span>{doc.display_name || '文档'}</span>
                              {doc.excerpt && <div className="persona-doc-excerpt">{doc.excerpt}</div>}
                            </div>
                          ))
                        ) : (
                          <p>请在企业资料页面上传 PRD / API 规范文档，缺陷将自动关联到对应文档出处。</p>
                        )}
                      </div>
                      <div className="evidence-check-grid">
                        <div className="evidence-check-panel verified">
                          <div className="persona-kicker">已具备证据</div>
                          <ul>{(quality.verified?.length ? quality.verified : ['暂无可交付证据']).map((item) => <li key={item}>{item}</li>)}</ul>
                        </div>
                        <div className="evidence-check-panel missing">
                          <div className="persona-kicker">企业验收缺口</div>
                          <ul>{(quality.missing || []).map((item) => <li key={item}>{item}</li>)}</ul>
                        </div>
                      </div>
                    </div>
                  )}

                  {pv === 'test' && (
                    <div className="persona-panel">
                      <div className="persona-section success">
                        <div className="persona-kicker">复现状态</div>
                        <div className="repro-status-bar">
                          <span className={`repro-status-badge ${f.is_reproducible ? 'reproducible' : 'not-reproducible'}`}>
                            {f.is_reproducible ? '✓ 可稳定复现' : '✗ 未通过复现门控'}
                          </span>
                          <span className="repro-confidence">复现置信度: {Math.round((f.confidence || 0) * 100)}%</span>
                          <span className="repro-count">已复现次数: {f.reproducibility_count || 0}</span>
                        </div>
                        {f.test_summary && <p className="repro-summary">{f.test_summary}</p>}
                      </div>
                      <div className="persona-section success">
                        <div className="persona-kicker">复现步骤</div>
                        {(repro.steps?.length || 0) > 0 ? (
                          <ol className="persona-step-list">
                            {repro.steps.map((step, i) => <li key={i}><code>{step}</code></li>)}
                          </ol>
                        ) : (
                          <div className="persona-empty-note strong">当前没有真实复现脚本或浏览器操作录屏，不能作为企业验收证据。请按下方“下一步采证”补跑。</div>
                        )}
                      </div>
                      {f.expected_actual_comparison && (
                        <div className="persona-section neutral">
                          <div className="persona-kicker">预期 vs 实际对比</div>
                          {f.expected_actual_comparison.difference && <p className="compare-difference">{f.expected_actual_comparison.difference}</p>}
                          <div className="persona-compare-grid">
                            <div className="persona-compare-card danger">
                              <span>实际行为</span>
                              <code>{f.expected_actual_comparison.actual || '未采集到真实响应体、截图或运行日志'}</code>
                            </div>
                            <div className="persona-compare-card success">
                              <span>预期行为</span>
                              <code>{f.expected_actual_comparison.expected || '未关联 PRD / API 规范'}</code>
                            </div>
                          </div>
                        </div>
                      )}
                      <div className="persona-section neutral">
                        <div className="persona-kicker">下一步采证</div>
                        <ol className="persona-step-list">
                          {(quality.next_actions?.length ? quality.next_actions : ['补充真实执行证据后重新生成缺陷报告']).map((step, i) => <li key={i}><code>{step}</code></li>)}
                        </ol>
                      </div>
                      {realReproEntry && <button className="btn btn-primary btn-sm" onClick={() => setReplayFinding(f)}>点击复现</button>}
                      <div className="persona-footnote">
                        {f.bug_status === 'reproduced' ? '以上证据可追溯到原始资料与验证动作，支持验收与复盘。' : '当前证据仍有缺口，请补齐真实请求、日志、DB 快照或文档出处后再进入企业缺陷交付。'}
                      </div>
                    </div>
                  )}

                  {pv === 'dev' && (
                    <div className="persona-panel">
                      {f.dev_summary && (
                        <div className="persona-section hero">
                          <div className="persona-kicker">研发定位摘要</div>
                          <p className="persona-summary">{f.dev_summary}</p>
                        </div>
                      )}
                      {f.technical_details && (
                        <div className="persona-section neutral">
                          <div className="persona-kicker">技术定位</div>
                          <div className="dev-technical-details">
                            <div className="dev-tech-row">
                              <span><em>涉及接口</em><code>{f.technical_details.api_endpoint.method} {f.technical_details.api_endpoint.path}</code></span>
                              {f.technical_details.api_endpoint.actor && <span><em>操作者</em><code>{formatActorName(f.technical_details.api_endpoint.actor)}</code></span>}
                            </div>
                            {f.technical_details.response_status > 0 && (
                              <div className="dev-tech-row">
                                <span><em>响应状态码</em><code>{f.technical_details.response_status}</code></span>
                                {f.technical_details.related_tables?.length > 0 && <span><em>关联表</em><code>{f.technical_details.related_tables.join(', ')}</code></span>}
                              </div>
                            )}
                            {f.technical_details.code_module_hint && (
                              <div className="dev-tech-row">
                                <span><em>代码模块</em><code>{f.technical_details.code_module_hint}</code></span>
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                      {f.technical_details?.possible_root_cause && (
                        <div className="persona-section warning">
                          <div className="persona-kicker">可能的根因方向</div>
                          <p className="root-cause-text">{f.technical_details.possible_root_cause}</p>
                        </div>
                      )}
                      {f.recommended_fix && (
                        <div className="persona-section success">
                          <div className="persona-kicker">修复建议</div>
                          <p className="fix-suggestion-text">{f.recommended_fix}</p>
                        </div>
                      )}
                      {f.regression_suggestions?.length > 0 && (
                        <div className="persona-section neutral">
                          <div className="persona-kicker">回归测试建议</div>
                          <ol className="persona-step-list">
                            {f.regression_suggestions.map((suggestion, i) => <li key={i}><code>{suggestion}</code></li>)}
                          </ol>
                        </div>
                      )}
                      {inv.trace_id && (
                        <div className="dev-trace-panel">
                          <div className="persona-kicker success">追踪标识（用于查日志）</div>
                          <div className="dev-trace-content">
                            <code className="dev-trace-id">{inv.trace_id}</code>
                            <CopyButton text={inv.trace_id} />
                          </div>
                          <div className="dev-trace-hint">在企业日志系统中搜索此 TraceID 可定位完整的请求链路、调用栈和异常堆栈</div>
                        </div>
                      )}
                      <div className="dev-debug-panel">
                        <div className="persona-kicker dark">调试信息</div>
                        {[
                          { label: '定位线索', code: inv.primary_area || f.source_entity || f.title },
                          { label: debugCurlCommand ? 'cURL 复现命令' : 'cURL 复现命令缺口', code: debugCurlCommand || '当前缺少真实复现脚本或可核验运行结果，暂不向客户展示推断命令。请补跑真实复现后再生成。' },
                          { label: 'SQL 核验建议', code: inv.sql_verify },
                          { label: '日志排查建议', code: inv.log_search },
                        ].map((item, idx) => (
                          <div key={idx} className="dev-debug-item">
                            <div className="dev-debug-head">
                              <span>{item.label}</span>
                              <CopyButton text={item.code} />
                            </div>
                            <code>{item.code}</code>
                          </div>
                        ))}
                      </div>
                      {f.raw_evidence?.has_real_evidence && (
                        <div className="persona-section neutral">
                          <div className="persona-kicker">原始证据（机器可追溯）</div>
                          <div className="raw-evidence-panel">
                            {f.raw_evidence.request_raw?.path && (
                              <div className="raw-evidence-item">
                                <span className="raw-evidence-label">请求</span>
                                <code>{f.raw_evidence.request_raw.method} {f.raw_evidence.request_raw.path}{f.raw_evidence.request_raw.actor ? ` (操作者: ${formatActorName(f.raw_evidence.request_raw.actor)})` : ''}</code>
                              </div>
                            )}
                            {f.raw_evidence.response_raw?.status_code ? (
                              <div className="raw-evidence-item">
                                <span className="raw-evidence-label">响应</span>
                                <code>HTTP {f.raw_evidence.response_raw.status_code} · {formatDurationMs(f.raw_evidence.response_raw.duration_ms)}</code>
                                {f.raw_evidence.response_raw.body && <pre className="raw-evidence-body">{f.raw_evidence.response_raw.body.slice(0, 500)}</pre>}
                              </div>
                            ) : null}
                            {f.raw_evidence.db_snapshot?.table && (
                              <div className="raw-evidence-item">
                                <span className="raw-evidence-label">DB快照</span>
                                <code>{f.raw_evidence.db_snapshot.table}.{f.raw_evidence.db_snapshot.column} = {f.raw_evidence.db_snapshot.value}</code>
                                {f.raw_evidence.db_snapshot.violation && <p className="raw-evidence-violation">{f.raw_evidence.db_snapshot.violation}</p>}
                              </div>
                            )}
                            {f.raw_evidence.logs?.trace_id && (
                              <div className="raw-evidence-item">
                                <span className="raw-evidence-label">日志</span>
                                <code>TraceID: {f.raw_evidence.logs.trace_id}</code>
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}

              <div className="evidence-proof">
                <svg viewBox="0 0 24 24" width="16" height="16"><path d="M20 6 9 17l-5-5" /></svg>
                <div>
                  <strong>验收判断 {realReproEntry ? '可复验' : '需补证'} · 复现率 {f.proof?.repro_rate ?? 0}%</strong>
                  <p>{quality.label} · {quality.score}/100 · {quality.summary}</p>
                </div>
              </div>
            </div>
          </article>
        );
      })}

      <EnterpriseDocuments project={project} />

      {replayFinding && (
        <Suspense fallback={<div className="replay-loading"><div className="spinner spinner-centered" /></div>}>
          <ReplayViewer projectId={project} finding={replayFinding} onClose={() => setReplayFinding(null)} />
        </Suspense>
      )}
    </div>
  );
}

function EnterpriseDocuments({ project }: { project: string }) {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [previewContent, setPreviewContent] = useState('');

  useEffect(() => {
    if (!project) return;
    setLoading(true);
    getKnowledgeAsset(project).then((data: unknown) => {
      const payload = (data && typeof data === 'object' ? data : {}) as any;
      const sources = payload.knowledge_asset?.sources || payload.source_inventory || [];
      const rawDocs = Array.isArray(sources) ? sources.filter((item): item is KnowledgeDocument => Boolean(item) && typeof item === 'object') : [];
      setDocs(rawDocs);
    }).catch(() => setDocs([])).finally(() => setLoading(false));
  }, [project]);

  const loadPreview = async (sourceId: string) => {
    if (previewId === sourceId) { setPreviewId(null); setPreviewContent(''); return; }
    setPreviewId(sourceId);
    try {
      const data = (await getKnowledgePreview(sourceId)) as any;
      setPreviewContent(typeof data.content === 'string' ? data.content : '无法加载文档内容');
    } catch { setPreviewContent('加载失败'); }
  };

  if (!project || (docs.length === 0 && !loading)) return null;

  return (
    <div className="evidence-docs-panel">
      <div className="evidence-docs-head">
        <span>企业资料 ({docs.length})</span>
        <Link to={buildProjectPath('/materials', project)}>前往管理</Link>
      </div>
      {loading ? <div className="evidence-docs-loading">加载中...</div> : (
        <div className="evidence-docs-list">
          {docs.map((doc) => {
            const docId = doc.source_id || doc.id || '';
            return (
              <div key={docId || doc.filename || doc.display_name}>
                <div onClick={() => { if (docId) void loadPreview(docId); }}
                  className={`evidence-doc-row${previewId === docId ? ' active' : ''}${docId ? ' clickable' : ''}`}>
                  <span>{doc.display_name || doc.filename || doc.original_name || doc.source_id || '未命名文档'}</span>
                  <span className="evidence-doc-type">{doc.type || doc.source_type || '文档'}</span>
                </div>
                {previewId === docId && <div className="evidence-doc-preview">{previewContent || '加载中...'}</div>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button className={`copy-button${copied ? ' copied' : ''}`}
      onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
      {copied ? '已复制' : '复制'}
    </button>
  );
}

export default EvidenceChain;
