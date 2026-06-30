import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useFindingsData } from '../api/data';
import type { Finding } from '../types';

type EvidenceFilter = 'all' | 'API' | 'DB' | '文档';

export function EvidenceChain() {
  const [params] = useSearchParams();
  const project = params.get('project') || 'real_project_demo';
  const { findings, loading } = useFindingsData(project);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<EvidenceFilter>('all');

  const withEvidence = findings.filter(f => f.evidence_chain.length >= 2);

  const apiEvidence = findings.filter(f => f.repro_path || f.title.toLowerCase().includes('api 接口') || f.title.toLowerCase().includes('接口')).length;
  const dbEvidence = findings.filter(f => f.title.includes('DB Verified') || f.title.includes('库存') || f.title.includes('BOM') || f.title.includes('数据库') || f.title.includes('DB')).length;
  const docEvidence = Math.max(0, findings.length - apiEvidence - dbEvidence);

  const displayData = (() => {
    if (filter === 'all') return withEvidence;
    if (filter === 'API') return withEvidence.filter(f => f.repro_path || f.title.toLowerCase().includes('api') || f.title.includes('接口'));
    if (filter === 'DB') return withEvidence.filter(f => f.title.includes('DB Verified') || f.title.includes('库存') || f.title.includes('BOM') || f.title.includes('数据库'));
    if (filter === '文档') return withEvidence.filter(f => !f.repro_path && !f.title.includes('DB Verified') && !f.title.includes('库存') && !f.title.includes('BOM') && !f.title.includes('数据库'));
    return withEvidence;
  })();

  function getEvidenceSources(f: Finding): string[] {
    const sources: string[] = [];
    const t = f.title.toLowerCase();
    const chain = f.evidence_chain.map(s => s.detail).join(' ');
    if (f.repro_path) sources.push('OpenAPI 规范');
    if (t.includes('db verified') || t.includes('库存') || chain.includes('db_verified')) sources.push('数据库 Schema');
    if (t.includes('prd') || chain.includes('PRD') || chain.includes('prd')) sources.push('PRD 文档');
    if (f.source_entity) sources.push('业务模型');
    if (sources.length === 0) sources.push('规范分析');
    return sources;
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>证据链</h1>
          <p>每条风险发现都关联到企业资料中的具体证据来源，确保可追溯、可验证</p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-5 gap-4 mb-4">
        {[
          { label: '证据链总数', val: withEvidence.length },
          { label: 'API 证据', val: apiEvidence, color: 'var(--primary)' },
          { label: 'DB 证据', val: dbEvidence, color: dbEvidence > 0 ? 'var(--danger)' : 'var(--success)' },
          { label: '文档证据', val: docEvidence, color: 'var(--warning)' },
          { label: '已确认', val: findings.filter(f => f.verdict === 'confirmed').length, color: 'var(--success)' },
        ].map(m => (
          <div key={m.label} className="stat-card" style={m.color ? { '--accent': m.color } as React.CSSProperties : {}}>
            <div className="cov-value" style={m.color ? { color: m.color } : {}}>{m.val}</div>
            <div className="cov-label">{m.label}</div>
          </div>
        ))}
      </div>

      {/* Filter */}
      <div className="filters mb-4">
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

      {loading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <div className="spinner" style={{ margin: '0 auto 16px' }} />
          <p style={{ color: 'var(--muted)', fontSize: 13 }}>加载证据链...</p>
        </div>
      )}

      {!loading && displayData.length === 0 && (
        <div style={{ textAlign: 'center', padding: 60, background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>🔗</div>
          <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>
            {filter !== 'all' ? `无 ${filter} 类型证据链` : '暂无证据链'}
          </h3>
          <p style={{ color: 'var(--muted)', fontSize: 13 }}>运行扫描发现行为风险后，证据链将自动生成</p>
          {filter !== 'all' && (
            <button className="btn btn-secondary mt-3" onClick={() => setFilter('all')} style={{ fontSize: 12 }}>← 查看全部</button>
          )}
        </div>
      )}

      {displayData.map(f => {
        const isOpen = expandedId === f.id;
        const sources = getEvidenceSources(f);

        return (
          <div key={f.id} className={`evidence-item ${f.severity.toLowerCase()}${isOpen ? ' open' : ''}`}>
            <div className="evidence-head" onClick={() => setExpandedId(isOpen ? null : f.id)}>
              <span className={`severity ${f.severity.toLowerCase()}`}>{f.severity}</span>
              <span className="evidence-title">{f.title}</span>
              <span className="evidence-meta">
                {sources.map(s => (
                  <span key={s} style={{
                    padding: '1px 7px', borderRadius: 3, fontSize: 9, fontWeight: 700,
                    background: s === 'OpenAPI 规范' ? 'var(--primary-muted)' : s === '数据库 Schema' ? 'var(--success-muted)' : s === 'PRD 文档' ? 'var(--warning-muted)' : '#f1f5f9',
                    color: s === 'OpenAPI 规范' ? 'var(--primary)' : s === '数据库 Schema' ? 'var(--success)' : s === 'PRD 文档' ? 'var(--warning)' : 'var(--muted)',
                  }}>{s}</span>
                ))}
                <time>{f.timestamp}</time>
              </span>
              <span className="evidence-expand">{isOpen ? '▲' : '▼'}</span>
            </div>
            <div className="evidence-body" style={{ display: isOpen ? 'block' : 'none' }}>
              {/* Evidence timeline */}
              {f.evidence_chain.length > 0 && (
                <div style={{ padding: '18px 0 8px' }}>
                  {f.evidence_chain.map((step, i) => (
                    <div key={i} style={{
                      display: 'flex', gap: 0, alignItems: 'flex-start',
                      position: 'relative', paddingBottom: i < f.evidence_chain.length - 1 ? 20 : 0,
                    }}>
                      {/* Timeline dot + line */}
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 24, flexShrink: 0 }}>
                        <div style={{
                          width: 10, height: 10, borderRadius: '50%',
                          background: step.tag === 'fact' ? 'var(--danger)' : step.tag === 'api' ? 'var(--primary)' : 'var(--warning)',
                          border: '2px solid #fff',
                          boxShadow: '0 0 0 2px ' + (step.tag === 'fact' ? 'var(--danger-muted)' : step.tag === 'api' ? 'var(--primary-muted)' : 'var(--warning-muted)'),
                        }} />
                        {i < f.evidence_chain.length - 1 && (
                          <div style={{ width: 2, flex: 1, background: 'var(--line)', marginTop: 4 }} />
                        )}
                      </div>
                      {/* Step content */}
                      <div style={{ flex: 1, paddingBottom: 4 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <span className={`step-tag ${step.tag}`}>{step.label}</span>
                        </div>
                        <strong style={{ display: 'block', fontSize: 13, marginBottom: 3 }}>{step.content}</strong>
                        {step.detail && (
                          <code style={{ fontSize: 11, color: 'var(--muted)', wordBreak: 'break-all' }}>{step.detail}</code>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Evidence sources summary */}
              <div style={{ marginTop: 8, padding: 14, background: '#f8fafc', borderRadius: 8, border: '1px solid var(--line)' }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 8 }}>
                  📎 证据来源
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {sources.map(s => (
                    <span key={s} style={{
                      padding: '4px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                      background: s === 'OpenAPI 规范' ? 'var(--primary-muted)' : s === '数据库 Schema' ? 'var(--success-muted)' : s === 'PRD 文档' ? 'var(--warning-muted)' : '#f1f5f9',
                      color: s === 'OpenAPI 规范' ? 'var(--primary)' : s === '数据库 Schema' ? 'var(--success)' : s === 'PRD 文档' ? 'var(--warning)' : 'var(--muted)',
                    }}>{s}</span>
                  ))}
                </div>
                <div style={{ marginTop: 8, fontSize: 11, color: 'var(--muted)' }}>
                  该证据链由以上企业资料交叉验证得出，可追溯到原始文档条目
                </div>
              </div>

              {/* Proof */}
              <div className="evidence-proof">
                <svg viewBox="0 0 24 24" width="16" height="16"><path d="M20 6 9 17l-5-5" /></svg>
                <div>
                  <strong>证据标识: {f.proof.hash}</strong>
                  <code>复现率: {f.proof.repro_rate}%</code>
                  {f.repro_path && <code style={{ marginTop: 2 }}>路径: {f.repro_method} {f.repro_path}</code>}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
