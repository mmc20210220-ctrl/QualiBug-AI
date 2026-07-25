import type { Finding } from '../../types';
import { EvidenceTimeline } from '../EvidenceTimeline';

interface EvidenceDrawerProps {
  finding: Finding | null;
  onClose: () => void;
}

export function EvidenceDrawer({ finding, onClose }: EvidenceDrawerProps) {
  if (!finding) return null;
  const quality = finding.evidence_quality;
  const chain = finding.evidence_chain || [];

  return (
    <>
      <div className={`evidence-drawer-backdrop${finding ? ' open' : ''}`} onClick={onClose} />
      <div className={`evidence-drawer${finding ? ' open' : ''}`}>
        <div className="evidence-drawer-head">
          <div>
            <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
            <strong style={{ marginLeft: 8, fontSize: 14 }}>{finding.title}</strong>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={onClose}>关闭</button>
        </div>
        <div className="evidence-drawer-body">
          {/* 证据质量 */}
          <div className="quality-score">
            <div className="quality-score-info">
              <h4>证据质量：{quality?.label || '已归档'}</h4>
              <p>{quality?.summary || ''} · 评分 {quality?.score ?? 0}/100</p>
              <div className="quality-dimensions">
                {(quality?.verified || []).map((v) => <span key={v} className="quality-dim">{v}</span>)}
                {(quality?.missing || []).map((m) => <span key={m} className="quality-dim missing">{m}</span>)}
              </div>
            </div>
          </div>

          {/* 预期 vs 实际 */}
          <h4 style={{ fontSize: 13, fontWeight: 700, margin: '16px 0 8px' }}>预期 vs 实际</h4>
          <div className="assertion-diff">
            <div className="assertion-diff-row">
              <span className="assertion-diff-label expected">预期</span>
              <span className="assertion-diff-value">{finding.expected || finding.expected_actual_comparison?.expected || '未指定'}</span>
            </div>
            <div className="assertion-diff-row">
              <span className="assertion-diff-label actual">实际</span>
              <span className="assertion-diff-value">{finding.actual || finding.expected_actual_comparison?.actual || '未捕获'}</span>
            </div>
          </div>

          {/* 证据链时间线 */}
          {chain.length > 0 && (
            <>
              <h4 style={{ fontSize: 13, fontWeight: 700, margin: '16px 0 8px' }}>证据链</h4>
              <EvidenceTimeline steps={chain} />
            </>
          )}

          {/* 复现信息 */}
          {finding.reproduction?.curl_command && (
            <div style={{ marginTop: 16 }}>
              <h4 style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>复现命令</h4>
              <pre style={{ fontSize: 12, background: 'var(--surface-2)', padding: 12, borderRadius: 8, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                <code>{finding.reproduction.curl_command}</code>
              </pre>
            </div>
          )}

          {/* 业务影响 */}
          <div style={{ marginTop: 16 }}>
            <h4 style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>业务影响</h4>
            <p style={{ fontSize: 13, color: 'var(--muted)' }}>
              {finding.business_impact?.summary || finding.business_summary || '该问题已形成确认结论。'}
            </p>
            {finding.business_impact?.module && (
              <p style={{ fontSize: 12, color: 'var(--subtle)', marginTop: 4 }}>
                影响模块：{finding.business_impact.module} · 紧急程度：{finding.business_impact.urgency || finding.severity}
              </p>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
