import { useState } from 'react';
import type { Finding } from '../types';
import { formatResponseSummary } from '../lib/display';
import { formatBeijingDateTime } from '../lib/time';

interface EvidenceFeedProps { findings: Finding[] }

export function EvidenceFeed({ findings }: EvidenceFeedProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'P0' | 'P1' | 'P2'>('all');

  const filtered = filter === 'all' ? findings : findings.filter(f => f.severity === filter);
  const displayFindings = filtered;

  const toggle = (id: string) => setExpandedId(expandedId === id ? null : id);
  const isOpen = (id: string) => expandedId === id;

  const filters: Array<{ label: string; value: typeof filter }> = [
    { label: '全部', value: 'all' }, { label: 'P0', value: 'P0' }, { label: 'P1', value: 'P1' }, { label: 'P2', value: 'P2' },
  ];

  const getStepContent = (step: Finding['evidence_chain'][number]) => {
    if (step.tag === 'response') {
      return formatResponseSummary(step.content || '', step.structured);
    }

    return step.content || '暂无内容';
  };

  return (
    <div>
      <div className="feed-header">
        <h2>行为验证 · 证据线索</h2>
        <div className="filters">
          {filters.map(f => (
            <button key={f.value} onClick={() => setFilter(f.value)}
              className={`filter${filter === f.value ? ' active' : ''}`}>{f.label}</button>
          ))}
        </div>
      </div>

      {displayFindings.length === 0 && (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">当前空态</span>
          <h3>暂无风险记录</h3>
          <p>运行扫描后，这里会自动沉淀为按严重级别可筛选的证据线索。</p>
        </section>
      )}

      {displayFindings.map(finding => (
        <div key={finding.id} className={`evidence-item ${finding.severity.toLowerCase()}${isOpen(finding.id) ? ' open' : ''}`}>
          <div className="evidence-head" onClick={() => toggle(finding.id)}>
            <span className={`severity ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
            <span className="evidence-title">{finding.title}</span>
            <span className="evidence-meta">
              <span>复现 {finding.reproducibility_count} 次</span>
              <time>{formatBeijingDateTime(finding.timestamp)}</time>
            </span>
            <span className="evidence-expand">▼</span>
          </div>
          <div className="evidence-body">
            <div className="evidence-chain">
              {finding.evidence_chain.map((step, i) => (
                <div className="chain-step" key={i}>
                  <span className={`step-tag ${step.tag}`}>{step.label}</span>
                  <strong>{getStepContent(step)}</strong>
                  <code>{step.detail}</code>
                </div>
              ))}
            </div>
            <div className="evidence-proof">
              <svg viewBox="0 0 24 24" width="16" height="16"><path d="M20 6 9 17l-5-5" /></svg>
              <div>
                <strong>证据验证{finding.proof.repro_rate >= 80 ? '通过' : '中'} · 复现率 {finding.proof.repro_rate}%</strong>
                <p>{finding.evidence_quality ? `${finding.evidence_quality.label} · ${finding.evidence_quality.score}/100 · ${finding.evidence_quality.summary}` : '检测证据已归档'}</p>
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
