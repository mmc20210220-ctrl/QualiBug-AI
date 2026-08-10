import { useState } from 'react';
import type { Finding } from '../../types';
import { buildFindingEvidencePackageHtml, buildFindingEvidencePackageText } from '../../lib/finding-evidence-package';
import { EvidenceTimeline } from '../EvidenceTimeline';

interface EvidenceDrawerProps {
  finding: Finding | null;
  onClose: () => void;
}

export function EvidenceDrawer({ finding, onClose }: EvidenceDrawerProps) {
  const [exportStatus, setExportStatus] = useState('');
  if (!finding) return null;
  const quality = finding.evidence_quality;
  const chain = finding.evidence_chain || [];

  const copyEvidencePackage = async () => {
    try {
      await navigator.clipboard.writeText(buildFindingEvidencePackageText(finding));
      setExportStatus('脱敏证据包已复制');
    } catch {
      setExportStatus('复制失败，请使用打印版证据包');
    }
    window.setTimeout(() => setExportStatus(''), 2500);
  };

  const openPrintableEvidencePackage = () => {
    const html = buildFindingEvidencePackageHtml(finding);
    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const opened = window.open('about:blank', '_blank');
    if (!opened) {
      URL.revokeObjectURL(url);
      setExportStatus('浏览器阻止了新窗口，请允许弹窗后重试');
      window.setTimeout(() => setExportStatus(''), 2500);
      return;
    }
    opened.opener = null;
    opened.location.href = url;
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    setExportStatus('已打开脱敏打印版证据包');
    window.setTimeout(() => setExportStatus(''), 2500);
  };

  return (
    <>
      <div className={`evidence-drawer-backdrop${finding ? ' open' : ''}`} onClick={onClose} />
      <div className={`evidence-drawer${finding ? ' open' : ''}`}>
        <div className="evidence-drawer-head">
          <div>
            <span className={`severity-badge ${finding.severity.toLowerCase()}`}>{finding.severity}</span>
            <strong style={{ marginLeft: 8, fontSize: 14 }}>{finding.title}</strong>
          </div>
          <div className="settings-actions">
            <button className="btn btn-secondary btn-sm" onClick={() => void copyEvidencePackage()}>复制脱敏证据包</button>
            <button className="btn btn-secondary btn-sm" onClick={openPrintableEvidencePackage}>打印 / PDF</button>
            <button className="btn btn-secondary btn-sm" onClick={onClose}>关闭</button>
          </div>
        </div>
        {exportStatus && <div className="settings-inline-feedback" role="status">{exportStatus}</div>}
        <div className="evidence-drawer-body">
          <div className="settings-card-note" style={{ marginBottom: 14 }}>
            外发操作只生成前端脱敏副本，不会创建公开链接或自动上传第三方。原始证据仍以当前证据中心为唯一事实源。
          </div>

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
              <p className="settings-hint">原始复现命令仅在登录后的证据中心展示；复制/打印外发包会重新执行脱敏，不直接复用该原始文本。</p>
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
