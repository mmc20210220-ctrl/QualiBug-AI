import { useState, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset, getKnowledgePreview } from '../api/client';
import { useFindingsData } from '../api/data';
import { getEvidenceLocatorText, getEvidenceLogHint, getEvidenceSqlHint, getEvidenceSummaryText } from '../lib/evidence';
import { usePageTitle } from '../lib/page-title';
import { formatBeijingDateTime } from '../lib/time';
import { buildProjectPath } from '../lib/project-navigation';

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
type KnowledgeAssetPayload = {
  knowledge_asset?: {
    sources?: unknown;
  };
  source_inventory?: unknown;
};
type KnowledgePreviewPayload = {
  content?: unknown;
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

  const withEvidence = findings.filter(f => f.evidence_chain.length >= 1);
  const apiEvidence = findings.filter(f => f.repro_path).length;
  const dbEvidence = findings.filter(f => f.title.includes('DB Verified') || f.title.includes('库存') || f.title.includes('BOM')).length;
  const docEvidence = findings.filter(f => !f.repro_path && !f.title.includes('DB Verified')).length;
  const displayData = (() => {
    if (filter === 'all') return withEvidence;
    if (filter === 'API') return withEvidence.filter(f => f.repro_path);
    if (filter === 'DB') return withEvidence.filter(f => f.title.includes('DB Verified') || f.title.includes('库存') || f.title.includes('BOM'));
    if (filter === '文档') return withEvidence.filter(f => !f.repro_path && !f.title.includes('DB Verified'));
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
            <span className="summary-pill">已确认 {findings.filter(f => f.verdict === 'confirmed').length}</span>
          </div>
        </div>
      </div>

      <div className="evidence-stat-grid mb-4">
        {[
          { label: '证据链总数', val: withEvidence.length, tone: '' },
          { label: 'API 证据', val: apiEvidence, tone: 'tone-primary' },
          { label: 'DB 证据', val: dbEvidence, tone: dbEvidence > 0 ? 'tone-danger' : 'tone-success' },
          { label: '文档证据', val: docEvidence, tone: 'tone-warning' },
          { label: '已确认', val: findings.filter(f => f.verdict === 'confirmed').length, tone: 'tone-success' },
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
          <button key={f.value} onClick={() => setFilter(f.value)}
            className={`filter${filter === f.value ? ' active' : ''}`}>{f.label}</button>
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

      {/* ── Enterprise Documents Panel ── */}
      <EnterpriseDocuments project={project} />

      {displayData.map(f => {
        const isOpen = expandedId === f.id;
        const pv = persona(f.id);
        const quality = f.evidence_quality;
        const debugItems = [
          { label: '定位线索', tone: 'tone-info', code: getEvidenceLocatorText(f) },
          {
            label: quality.curl_command ? 'cURL 复现命令' : 'cURL 复现命令缺口',
            tone: quality.curl_command ? 'tone-cyan' : 'tone-warning',
            code: quality.curl_command || '缺少可访问测试地址或真实运行结果，暂不能生成企业可复现 cURL。请先配置测试地址并补跑扫描。',
          },
          { label: 'SQL 核验建议', tone: 'tone-success', code: getEvidenceSqlHint(f) },
          { label: '日志排查建议', tone: 'tone-warning', code: getEvidenceLogHint(f) },
        ];

        return (
          <div key={f.id} className={`evidence-item ${f.severity.toLowerCase()}${isOpen ? ' open' : ''}`}>
            <div className="evidence-head" onClick={() => setExpandedId(isOpen ? null : f.id)}>
              <span className={`severity ${f.severity.toLowerCase()}`}>{f.severity}</span>
              <span className="evidence-title">{f.title}</span>
              <span className="evidence-meta">
                {f.source_entity && <span className="evidence-source-chip">{f.source_entity}</span>}
                <time>{formatBeijingDateTime(f.timestamp)}</time>
              </span>
              <span className="evidence-expand">{isOpen ? '▲' : '▼'}</span>
            </div>
            <div className="evidence-body">
              {/* Persona tabs */}
              <div className="persona-tabs">
                {personaTabs.map(t => (
                  <button key={t.key}
                    onClick={(e) => { e.stopPropagation(); setActivePersona(p => ({ ...p, [f.id]: t.key })); }}
                    className={`persona-tab${pv === t.key ? ' active' : ''}`}>
                    {t.label}
                    <span>{t.desc}</span>
                  </button>
                ))}
              </div>

              {/* === BUSINESS VIEW === */}
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
                    <p className="persona-summary">{f.business_impact?.summary || f.actual || '该缺陷可能导致业务流程异常，影响用户体验和业务数据一致性。'}</p>
                    <div className="persona-inline-meta">
                      <span><em>影响模块</em><strong>{f.business_impact?.module || f.source_entity || '核心业务'}</strong></span>
                      <span><em>紧急程度</em><strong>{businessUrgencyLabel(f.business_impact?.urgency, f.severity)}</strong></span>
                    </div>
                  </div>
                  <div className="persona-section neutral">
                    <div className="persona-kicker">业务描述</div>
                    <p>{f.expected || '系统在特定操作序列下未按预期业务规则响应，存在状态不一致风险。'}</p>
                  </div>
                  <div className="persona-section warning">
                    <div className="persona-kicker">需求来源</div>
                    {f.docRefs?.length ? (
                      f.docRefs.slice(0, 3).map((doc: { display_name?: string; excerpt?: string }, i: number) => (
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
                      <ul>
                        {(quality.verified.length ? quality.verified : ['暂无可交付证据']).map(item => <li key={item}>{item}</li>)}
                      </ul>
                    </div>
                    <div className="evidence-check-panel missing">
                      <div className="persona-kicker">企业验收缺口</div>
                      <ul>
                        {quality.missing.map(item => <li key={item}>{item}</li>)}
                      </ul>
                    </div>
                  </div>
                </div>
              )}

              {/* === TEST VIEW === */}
              {pv === 'test' && (
                <div className="persona-panel">
                  <div className="persona-section success">
                    <div className="persona-kicker">复现步骤</div>
                    {(f.repro_steps?.length || f.reproduce_steps_business?.length) ? (
                      <ol className="persona-step-list">
                        {(f.repro_steps?.length ? f.repro_steps : f.reproduce_steps_business).map((step: string, i: number) => (
                          <li key={i}><code>{step}</code></li>
                        ))}
                      </ol>
                    ) : (
                      <div className="persona-empty-note strong">
                        当前没有真实复现脚本或浏览器操作录屏，不能作为企业验收证据。请按下方“下一步采证”补跑。
                      </div>
                    )}
                  </div>
                  <div className="persona-compare-grid">
                    <div className="persona-compare-card danger">
                      <span>实际行为</span>
                      <code>{f.actual || '未采集到真实响应体、截图或运行日志，暂不能断言实际行为。'}</code>
                    </div>
                    <div className="persona-compare-card success">
                      <span>预期行为</span>
                      <code>{f.expected || '未关联 PRD / API 规范，暂不能给出可审计预期行为。'}</code>
                    </div>
                  </div>
                  <div className="persona-section neutral">
                    <div className="persona-kicker">下一步采证</div>
                    <ol className="persona-step-list">
                      {(quality.next_actions.length ? quality.next_actions : ['补充真实执行证据后重新生成缺陷报告']).map((step, i) => (
                        <li key={i}><code>{step}</code></li>
                      ))}
                    </ol>
                  </div>
                  <div className="persona-section warning">
                    <div className="persona-kicker">文档出处</div>
                    {f.docRefs?.length ? (
                      <div className="persona-doc-chip-row">
                        {f.docRefs.slice(0,3).map((d: { display_name?: string }, i: number) => (
                          <span key={i} className="persona-doc-chip">{d.display_name}</span>
                        ))}
                      </div>
                    ) : (
                      <div className="persona-empty-note">
                        暂无关联文档，前往 <Link to={buildProjectPath('/materials', project)}>企业资料</Link> 上传业务资料后会自动关联。
                      </div>
                    )}
                  </div>
                  <div className="persona-footnote">
                    {quality.level === 'validated'
                      ? '以上证据可追溯到原始资料与验证动作，支持验收与复盘。'
                      : '当前证据仍有缺口，请补齐真实请求、日志、DB 快照或文档出处后再进入企业缺陷交付。'}
                  </div>
                </div>
              )}

              {/* === DEV VIEW === */}
              {pv === 'dev' && (
                <div className="persona-panel">
                  <div className="dev-debug-panel">
                    <div className="persona-kicker dark">调试信息</div>
                    {debugItems.map((item, idx) => (
                      <div key={idx} className="dev-debug-item">
                        <div className="dev-debug-head">
                          <span>{item.label}</span>
                          <CopyButton text={item.code} />
                        </div>
                        <code className={item.tone}>
                          {item.code}
                        </code>
                      </div>
                    ))}
                  </div>
                  <div className="persona-section neutral">
                    <div className="persona-kicker">证据概览</div>
                    <div className="dev-evidence-meta">
                      <span>{getEvidenceSummaryText(f)}</span>
                      <span>复现率: {quality.can_reproduce ? f.proof?.repro_rate || 100 : 0}%</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Enterprise Documents Panel ──
function EnterpriseDocuments({ project }: { project: string }) {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [previewContent, setPreviewContent] = useState('');

  const normalizeDocLabel = (doc: KnowledgeDocument) =>
    doc.display_name || doc.filename || doc.original_name || doc.source_id || doc.id || '未命名文档';

  const normalizeDocType = (doc: KnowledgeDocument) => doc.type || doc.source_type || '文档';

  const normalizeDocKey = (doc: KnowledgeDocument) => {
    const label = (doc.filename || doc.original_name || doc.display_name || '').trim();
    if (label) return label.toLowerCase();
    const id = (doc.source_id || doc.id || '').trim();
    return id || 'unknown';
  };

  const normalizeDocId = (doc: KnowledgeDocument) => doc.source_id || doc.id || '';

  useEffect(() => {
    if (!project) return;
    setLoading(true);
    getKnowledgeAsset(project).then((data: unknown) => {
      const payload = (data && typeof data === 'object' ? data : {}) as KnowledgeAssetPayload;
      const sources = payload.knowledge_asset?.sources || payload.source_inventory || [];
      const rawDocs = Array.isArray(sources)
        ? sources.filter((item): item is KnowledgeDocument => Boolean(item) && typeof item === 'object')
        : [];
      const merged: Record<string, KnowledgeDocument> = {};
      const orderedKeys: string[] = [];
      for (const doc of rawDocs) {
        const key = normalizeDocKey(doc);
        const existing = merged[key];
        if (!existing) {
          merged[key] = doc;
          orderedKeys.push(key);
          continue;
        }
        const existingId = normalizeDocId(existing);
        const incomingId = normalizeDocId(doc);
        const existingHasPreview = Boolean(existing.stored_path) || existingId.startsWith('src_');
        const incomingHasPreview = Boolean(doc.stored_path) || incomingId.startsWith('src_');
        const existingIsInput = existingId.startsWith('input-');
        const incomingIsInput = incomingId.startsWith('input-');
        if ((incomingHasPreview && !existingHasPreview) || (!incomingIsInput && existingIsInput)) {
          merged[key] = doc;
        }
      }
      setDocs(orderedKeys.map((key) => merged[key]).filter(Boolean));
    }).catch(() => setDocs([])).finally(() => setLoading(false));
  }, [project]);

  const loadPreview = async (sourceId: string) => {
    if (previewId === sourceId) { setPreviewId(null); setPreviewContent(''); return; }
    setPreviewId(sourceId);
    try {
      const data = (await getKnowledgePreview(sourceId)) as KnowledgePreviewPayload;
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
            const docId = normalizeDocId(doc);
            return (
            <div key={docId || doc.filename || doc.display_name}>
              <div
                onClick={() => { if (docId) void loadPreview(docId); }}
                className={`evidence-doc-row${previewId === docId ? ' active' : ''}${docId ? ' clickable' : ''}`}
              >
                <span>{normalizeDocLabel(doc)}</span>
                <span className="evidence-doc-type">{normalizeDocType(doc)}</span>
              </div>
              {previewId === docId && (
                <div className="evidence-doc-preview">
                  {previewContent || '加载中...'}
                </div>
              )}
            </div>
          );
          })}
        </div>
      )}
    </div>
  );
}

// ── Copy button for dev view ──
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className={`copy-button${copied ? ' copied' : ''}`}
      onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
    >
      {copied ? '已复制' : '复制'}
    </button>
  );
}

export default EvidenceChain;
