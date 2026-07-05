import { useState, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset, getKnowledgePreview } from '../api/client';
import { useFindingsData } from '../api/data';
import { formatActorName, formatDurationMs } from '../lib/display';
import { usePageTitle } from '../lib/page-title';
import { formatBeijingDateTime } from '../lib/time';
import { buildProjectPath } from '../lib/project-navigation';
import { EvidenceTimeline } from '../components/EvidenceTimeline';
import { lazy, Suspense } from 'react';
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

export function EvidenceChain() {
  usePageTitle('证据链');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { findings, loading } = useFindingsData(project);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [activePersona, setActivePersona] = useState<Record<string, PersonaView>>({});
  const [filter, setFilter] = useState<EvidenceFilter>('all');
  const [replayFinding, setReplayFinding] = useState<Finding | null>(null);

  const withEvidence = findings.filter(f => (f.evidence_chain?.length || 0) >= 1);
  const apiEvidence = findings.filter(f => f.reproduction?.path || f.repro_path).length;
  const dbEvidence = findings.filter(f => f.risk_type?.includes('db') || f.defect_family === 'data_integrity').length;
  const docEvidence = findings.filter(f => !(f.reproduction?.path || f.repro_path) && !f.risk_type?.includes('db') && f.defect_family !== 'data_integrity').length;

  // 四态状态统计
  const reproducedCount = withEvidence.filter(f => f.bug_status === 'reproduced').length;
  const suspectedCount = withEvidence.filter(f => f.bug_status === 'suspected').length;
  const riskClueCount = withEvidence.filter(f => f.bug_status === 'risk_clue').length;
  const notReproducedCount = withEvidence.filter(f => f.bug_status === 'not_reproduced').length;
  const validatedEvidence = withEvidence.filter(f => f.evidence_quality?.level === 'validated').length;
  const evidenceClosedLoop = Math.round((reproducedCount / Math.max(withEvidence.length, 1)) * 100);

  const displayData = (() => {
    if (filter === 'all') return withEvidence;
    if (filter === 'API') return withEvidence.filter(f => f.reproduction?.path || f.repro_path);
    if (filter === 'DB') return withEvidence.filter(f => f.risk_type?.includes('db') || f.defect_family === 'data_integrity');
    if (filter === '文档') return withEvidence.filter(f => !(f.reproduction?.path || f.repro_path) && !f.risk_type?.includes('db') && f.defect_family !== 'data_integrity');
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
      <div className="page-header">
        <div>
          <span className="panel-kicker">证据工作台</span>
          <h1>证据链</h1>
          <p>把每条风险结论拆成业务语义、复现动作与技术溯源，确保可追溯、可复现、可审计。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">证据链 {withEvidence.length}</span>
            <span className="summary-pill">API 证据 {apiEvidence}</span>
            <span className="summary-pill">数据证据 {dbEvidence}</span>
            <span className="summary-pill">闭环率 {evidenceClosedLoop}%</span>
          </div>
        </div>
      </div>

      <div className="evidence-stat-grid mb-4">
        {[
          { label: '证据链总数', val: withEvidence.length, tone: '' },
          { label: '已复现 Bug', val: reproducedCount, tone: 'tone-success' },
          { label: '疑似问题', val: suspectedCount, tone: 'tone-warning' },
          { label: '风险线索', val: riskClueCount, tone: 'tone-muted' },
          { label: '闭环率', val: `${evidenceClosedLoop}%`, tone: evidenceClosedLoop >= 70 ? 'tone-success' : 'tone-warning' },
        ].map(m => (
          <article key={m.label} className={`evidence-stat-card${m.tone ? ` ${m.tone}` : ''}`}>
            <strong>{m.val}</strong>
            <span>{m.label}</span>
          </article>
        ))}
      </div>

      <div className="filters behavior-filters mb-4">
        {([
          { label: `全部 (${withEvidence.length})`, value: 'all' as EvidenceFilter },
          { label: `API (${apiEvidence})`, value: 'API' as EvidenceFilter },
          { label: `DB (${dbEvidence})`, value: 'DB' as EvidenceFilter },
          { label: `文档 (${docEvidence})`, value: '文档' as EvidenceFilter },
        ]).map(f => (
          <button key={f.value} onClick={() => setFilter(f.value)} className={`filter${filter === f.value ? ' active' : ''}`}>{f.label}</button>
        ))}
      </div>

      {loading && <section className="findings-empty-state compact"><div className="spinner spinner-centered" /><p>正在整理证据链...</p></section>}
      {!loading && displayData.length === 0 && (
        <section className="findings-empty-state">
          <span className="findings-empty-kicker">当前空态</span>
          <h3>{filter !== 'all' ? `无 ${filter} 类型证据链` : '暂无证据链'}</h3>
          <p>运行扫描发现行为风险后，证据链将自动生成并在这里按视角展开。</p>
        </section>
      )}

      <EnterpriseDocuments project={project} />

      {displayData.map(f => {
        const isOpen = expandedId === f.id;
        const pv = persona(f.id);
        const quality = f.evidence_quality || { level: 'needs_evidence', score: 0, label: '', summary: '', verified: [], missing: [], next_actions: [], can_reproduce: false, curl_command: '' };
        const repro = f.reproduction || { method: '', path: '', steps: [], curl_command: '' };
        const inv = f.investigation_guidance || { primary_area: '', relevant_apis: [], relevant_tables: [], log_search: '', sql_verify: '', trace_id: '' };
        const biz = f.business_impact || { summary: '', urgency: '', module: '' };

        return (
          <div key={f.id} className={`evidence-item ${f.severity.toLowerCase()} bug-status-${f.bug_status || 'risk_clue'}${isOpen ? ' open' : ''}`}>
            <div className="evidence-head" onClick={() => setExpandedId(isOpen ? null : f.id)}>
              <span className={`severity ${f.severity.toLowerCase()}`}>{f.severity}</span>
              <span className={`bug-status-badge bug-status-${f.bug_status || 'risk_clue'}`}>
                {f.bug_status_label || '风险线索'}
              </span>
              <span className="evidence-title">{f.title}</span>
              <span className="evidence-meta">
                <span className="evidence-quality-score-chip">{quality.score}/100</span>
                {f.source_entity && <span className="evidence-source-chip">{f.source_entity}</span>}
                {(f.affected_count || 0) > 1 && <span className="evidence-affected-chip">影响 {f.affected_count} 例</span>}
                <time>{formatBeijingDateTime(f.timestamp)}</time>
              </span>
              <span className="evidence-expand">{isOpen ? '▲' : '▼'}</span>
            </div>
            {/* 一句话结论 */}
            {f.business_summary && (
              <div className="evidence-one-liner" onClick={() => setExpandedId(isOpen ? null : f.id)}>
                <span className="evidence-one-liner-label">一句话结论</span>
                <span className="evidence-one-liner-text">{f.business_summary}</span>
              </div>
            )}
            {/* 降级提示横幅 */}
            {(f.bug_status === 'suspected' || f.bug_status === 'risk_clue') && (
              <div className="evidence-downgrade-banner">
                {f.bug_status === 'suspected' && (
                  <span>⚠ 证据不足，不能直接算 Bug。{(f.gate_failures || []).join('；')}</span>
                )}
                {f.bug_status === 'risk_clue' && (
                  <span>⚠ 风险线索，需要继续复现验证。当前缺少真实运行时证据。</span>
                )}
              </div>
            )}
            <div className="evidence-body">
              <div className={`evidence-dossier ${quality.level}`}>
                <div className="evidence-dossier-head">
                  <div>
                    <span className="panel-kicker">证据闭环概要</span>
                    <strong>{quality.label} · {quality.score}/100</strong>
                  </div>
                  <span className="evidence-dossier-status">{quality.can_reproduce ? '可复现' : '待补证'}</span>
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
                      {f.evidence_completeness.dimensions.map(d => (
                        <span key={d.key} className={`evidence-completeness-dim ${d.present ? 'present' : 'missing'}`}>
                          <span className="evidence-completeness-icon">{d.present ? '✓' : '○'}</span>
                          {d.label}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                <div className="evidence-dossier-proof">
                  <span><em>复现入口</em><strong>{repro.method || f.repro_method || 'GET'} {repro.path || f.repro_path || '待配置'}</strong></span>
                  <span><em>预期规则</em><strong>{f.expected ? '已记录' : '待关联'}</strong></span>
                  <span><em>实际结果</em><strong>{f.actual ? '已记录' : '待采集'}</strong></span>
                  <span><em>验收缺口</em><strong>{quality.missing?.length || 0}</strong></span>
                </div>
              </div>

              <div className="persona-tabs">
                {personaTabs.map(t => (
                  <button key={t.key}
                    onClick={(e) => { e.stopPropagation(); setActivePersona(p => ({ ...p, [f.id]: t.key })); }}
                    className={`persona-tab${pv === t.key ? ' active' : ''}`}>
                    {t.label}<span>{t.desc}</span>
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
                  {(f.failed_assertions?.length || 0) > 0 && (
                    <div className="persona-section danger">
                      <div className="persona-kicker">关键失败断言</div>
                      <div className="failed-assertions-list">
                        {f.failed_assertions!.map((fa, i) => (
                          <div key={i} className="failed-assertion-card">
                            <span className="failed-assertion-label">{fa.label}</span>
                            <div className="failed-assertion-compare">
                              <span><em>预期</em><code>{fa.expected}</code></span>
                              <span><em>实际</em><code>{fa.actual}</code></span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {(f.affected_instances?.length || 0) > 1 && (
                    <div className="persona-section warning">
                      <div className="persona-kicker">受影响实例（{f.affected_count} 例）</div>
                      <div className="affected-instances-list">
                        {f.affected_instances!.slice(0, 10).map((inst, i) => (
                          <span key={i} className="affected-instance-chip">{inst}</span>
                        ))}
                        {(f.affected_count || 0) > 10 && <span className="affected-instance-more">+{(f.affected_count || 0) - 10} 例</span>}
                      </div>
                    </div>
                  )}
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
                      <ul>{(quality.verified?.length ? quality.verified : ['暂无可交付证据']).map(item => <li key={item}>{item}</li>)}</ul>
                    </div>
                    <div className="evidence-check-panel missing">
                      <div className="persona-kicker">企业验收缺口</div>
                      <ul>{(quality.missing || []).map(item => <li key={item}>{item}</li>)}</ul>
                    </div>
                  </div>
                </div>
              )}

              {pv === 'test' && (
                <div className="persona-panel">
                  {/* 复现稳定性信息 */}
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
                      <div className="persona-empty-note strong">当前没有真实复现脚本或浏览器操作录屏，不能作为企业验收证据。请按下方"下一步采证"补跑。</div>
                    )}
                  </div>
                  {/* 预期 vs 实际 结构化对比 */}
                  {f.expected_actual_comparison && (
                    <div className="persona-section neutral">
                      <div className="persona-kicker">预期 vs 实际对比</div>
                      {f.expected_actual_comparison.difference && (
                        <p className="compare-difference">{f.expected_actual_comparison.difference}</p>
                      )}
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
                      {f.expected_actual_comparison.api_comparison && (
                        <div className="compare-api-detail">
                          <span><em>API 响应</em><code>{f.expected_actual_comparison.api_comparison.expected} → {f.expected_actual_comparison.api_comparison.actual}</code></span>
                        </div>
                      )}
                      {f.expected_actual_comparison.db_comparison && (
                        <div className="compare-db-detail">
                          <span><em>DB 字段</em><code>{f.expected_actual_comparison.db_comparison.table}.{f.expected_actual_comparison.db_comparison.column}: {f.expected_actual_comparison.db_comparison.actual}</code></span>
                        </div>
                      )}
                    </div>
                  )}
                  {/* 失败断言 */}
                  {(f.failed_assertions?.length || 0) > 0 && (
                    <div className="persona-section danger">
                      <div className="persona-kicker">失败断言（{f.failed_assertions!.length} 项）</div>
                      <div className="failed-assertions-list">
                        {f.failed_assertions!.map((fa, i) => (
                          <div key={i} className="failed-assertion-card">
                            <span className="failed-assertion-label">{fa.label}</span>
                            <div className="failed-assertion-compare">
                              <span><em>预期</em><code>{fa.expected}</code></span>
                              <span><em>实际</em><code>{fa.actual}</code></span>
                            </div>
                            {fa.detail && <p className="failed-assertion-detail">{fa.detail}</p>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="persona-section neutral">
                    <div className="persona-kicker">下一步采证</div>
                    <ol className="persona-step-list">
                      {(quality.next_actions?.length ? quality.next_actions : ['补充真实执行证据后重新生成缺陷报告']).map((step, i) => <li key={i}><code>{step}</code></li>)}
                    </ol>
                  </div>
                  {repro.path && (
                    <button className="btn btn-primary btn-sm" onClick={() => setReplayFinding(f)}>点击复现</button>
                  )}
                  <div className="persona-footnote">
                    {f.bug_status === 'reproduced' ? '以上证据可追溯到原始资料与验证动作，支持验收与复盘。' : '当前证据仍有缺口，请补齐真实请求、日志、DB 快照或文档出处后再进入企业缺陷交付。'}
                  </div>
                </div>
              )}

              {pv === 'dev' && (
                <div className="persona-panel">
                  {/* 一句话研发定位 */}
                  {f.dev_summary && (
                    <div className="persona-section hero">
                      <div className="persona-kicker">研发定位摘要</div>
                      <p className="persona-summary">{f.dev_summary}</p>
                    </div>
                  )}
                  {/* 技术定位详情 */}
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
                            {f.technical_details.related_tables?.length > 0 && (
                              <span><em>关联表</em><code>{f.technical_details.related_tables.join(', ')}</code></span>
                            )}
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
                  {/* 可能的根因 */}
                  {f.technical_details?.possible_root_cause && (
                    <div className="persona-section warning">
                      <div className="persona-kicker">可能的根因方向</div>
                      <p className="root-cause-text">{f.technical_details.possible_root_cause}</p>
                    </div>
                  )}
                  {/* 修复建议 */}
                  {f.recommended_fix && (
                    <div className="persona-section success">
                      <div className="persona-kicker">修复建议</div>
                      <p className="fix-suggestion-text">{f.recommended_fix}</p>
                    </div>
                  )}
                  {/* 回归测试建议 */}
                  {f.regression_suggestions?.length > 0 && (
                    <div className="persona-section neutral">
                      <div className="persona-kicker">回归测试建议</div>
                      <ol className="persona-step-list">
                        {f.regression_suggestions.map((s, i) => <li key={i}><code>{s}</code></li>)}
                      </ol>
                    </div>
                  )}
                  {/* TraceID 优先展示——研发查日志的核心入口 */}
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
                      { label: quality.curl_command ? 'cURL 复现命令' : 'cURL 复现命令缺口', code: quality.curl_command || '缺少可访问测试地址或真实运行结果，暂不能生成企业可复现 cURL。请先配置测试地址并补跑扫描。' },
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
                  {/* 原始证据 */}
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
                        {f.raw_evidence.execution_trace?.source_file && (
                          <div className="raw-evidence-item">
                            <span className="raw-evidence-label">执行轨迹</span>
                            <code>{f.raw_evidence.execution_trace.source_file}</code>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                  <div className="persona-section neutral">
                    <div className="persona-kicker">证据概览</div>
                    <div className="dev-evidence-meta">
                      <span>{quality.label} · {quality.score}/100 · {quality.summary}</span>
                      <span>复现率: {quality.can_reproduce ? f.proof?.repro_rate || 100 : 0}%</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })}

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
