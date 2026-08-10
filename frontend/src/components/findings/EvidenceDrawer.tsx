import { useEffect, useState } from 'react';
import {
  createEvidenceShare,
  listEvidenceShares,
  revokeEvidenceShare,
  type EvidenceShareMetadata,
} from '../../api/evidence-share';
import type { CollaborativeFindingProjection } from '../../api/finding-collaboration';
import type { Finding } from '../../types';
import { buildFindingEvidencePackageHtml, buildFindingEvidencePackageText } from '../../lib/finding-evidence-package';
import { EvidenceTimeline } from '../EvidenceTimeline';

interface EvidenceDrawerProps {
  finding: Finding | null;
  project: string;
  onClose: () => void;
}

type ShareableFinding = Finding & CollaborativeFindingProjection;

const TTL_OPTIONS = [
  { value: 60 * 60, label: '1 小时' },
  { value: 24 * 60 * 60, label: '24 小时' },
  { value: 3 * 24 * 60 * 60, label: '3 天' },
  { value: 7 * 24 * 60 * 60, label: '7 天' },
];

export function EvidenceDrawer({ finding, project, onClose }: EvidenceDrawerProps) {
  const [exportStatus, setExportStatus] = useState('');
  const [shareStatus, setShareStatus] = useState('');
  const [shareTtl, setShareTtl] = useState(24 * 60 * 60);
  const [shares, setShares] = useState<EvidenceShareMetadata[]>([]);
  const [sharesLoading, setSharesLoading] = useState(false);
  const [shareSaving, setShareSaving] = useState(false);
  const [generatedShareUrl, setGeneratedShareUrl] = useState('');
  const persistenceId = finding
    ? ((finding as ShareableFinding).finding_persistence_id || '')
    : '';

  useEffect(() => {
    setGeneratedShareUrl('');
    setShareStatus('');
    if (!finding || !project || !persistenceId) {
      setShares([]);
      setSharesLoading(false);
      return;
    }

    let cancelled = false;
    setSharesLoading(true);
    listEvidenceShares(project, persistenceId)
      .then((items) => {
        if (!cancelled) setShares(items);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setShares([]);
          setShareStatus(error instanceof Error ? error.message : '分享记录读取失败');
        }
      })
      .finally(() => {
        if (!cancelled) setSharesLoading(false);
      });
    return () => { cancelled = true; };
  }, [finding, persistenceId, project]);

  if (!finding) return null;
  const quality = finding.evidence_quality;
  const chain = finding.evidence_chain || [];

  const refreshShares = async () => {
    if (!project || !persistenceId) return;
    const items = await listEvidenceShares(project, persistenceId);
    setShares(items);
  };

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

  const createShare = async () => {
    if (!persistenceId || !project || shareSaving) return;
    setShareSaving(true);
    setShareStatus('正在生成只读脱敏快照…');
    setGeneratedShareUrl('');
    try {
      const share = await createEvidenceShare(project, persistenceId, shareTtl);
      const url = `${window.location.origin}${share.share_path}`;
      setGeneratedShareUrl(url);
      try {
        await navigator.clipboard.writeText(url);
        setShareStatus(`只读链接已生成并复制，有效期至 ${share.expires_at}`);
      } catch {
        setShareStatus(`只读链接已生成，有效期至 ${share.expires_at}；请从下方输入框手动复制。`);
      }
      await refreshShares();
    } catch (error: unknown) {
      setShareStatus(error instanceof Error ? error.message : '只读分享链接创建失败');
    } finally {
      setShareSaving(false);
    }
  };

  const revokeShare = async (shareId: string) => {
    if (!project || !shareId) return;
    setShareStatus('正在撤销分享…');
    try {
      await revokeEvidenceShare(project, shareId);
      if (generatedShareUrl) setGeneratedShareUrl('');
      await refreshShares();
      setShareStatus('分享链接已撤销，原 Token 将立即失效。');
    } catch (error: unknown) {
      setShareStatus(error instanceof Error ? error.message : '分享链接撤销失败');
    }
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
            复制/打印只生成本地脱敏副本；需要跨团队访问时，可额外创建有时效、可撤销的只读分享链接。公开链接只能读取创建当刻冻结的脱敏快照，不能访问项目后台或原始凭据。
          </div>

          <section className="card mb-4" aria-label="只读证据分享">
            <div className="settings-card-head">
              <div>
                <span className="panel-kicker">安全分发</span>
                <h3>临时只读分享</h3>
                <p className="muted">Token 只返回一次，数据库仅保存哈希；过期或撤销后无法继续解析。</p>
              </div>
              <div className="settings-actions">
                <select
                  className="form-input"
                  value={shareTtl}
                  onChange={(event) => setShareTtl(Number(event.target.value))}
                  aria-label="分享有效期"
                  disabled={!persistenceId || shareSaving}
                >
                  {TTL_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => void createShare()}
                  disabled={!persistenceId || shareSaving}
                >
                  {shareSaving ? '生成中…' : '生成只读链接'}
                </button>
              </div>
            </div>

            {!persistenceId && (
              <p className="settings-inline-feedback">
                当前 Finding 尚未唯一绑定持久化 ID，因此禁止生成公开链接，避免分享错问题。
              </p>
            )}

            {generatedShareUrl && (
              <div className="form-group mt-3">
                <label className="form-label">本次生成的链接（明文 Token 仅本次可见）</label>
                <div className="settings-actions">
                  <input className="form-input form-input-mono" readOnly value={generatedShareUrl} />
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => void navigator.clipboard.writeText(generatedShareUrl)}
                  >
                    复制链接
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => window.open(generatedShareUrl, '_blank', 'noopener,noreferrer')}
                  >
                    打开预览
                  </button>
                </div>
              </div>
            )}

            {shareStatus && <p className="settings-inline-feedback" role="status">{shareStatus}</p>}

            <details className="settings-auth-section mt-3">
              <summary>
                <strong>已创建分享</strong>
                <span className="muted">{sharesLoading ? '读取中…' : `${shares.filter((item) => item.active).length} 个有效`}</span>
              </summary>
              <div className="settings-system-list mt-3">
                {!sharesLoading && shares.length === 0 && <p className="muted">当前没有分享记录。</p>}
                {shares.map((share) => (
                  <div className="settings-service-row" key={share.share_id}>
                    <div className="settings-service-copy">
                      <div className="settings-service-name">
                        {share.active ? '有效只读链接' : share.revoked ? '已撤销' : '已过期'}
                        <span className={`settings-service-badge ${share.active ? 'enabled' : 'disabled'}`}>{share.active ? '可访问' : '失效'}</span>
                      </div>
                      <div className="settings-service-meta">
                        创建 {share.created_at || '未知'} · 到期 {share.expires_at || '未知'}
                        {share.created_by ? ` · 创建人 ${share.created_by}` : ''}
                      </div>
                    </div>
                    {share.active && (
                      <button type="button" className="btn btn-secondary settings-btn-mini" onClick={() => void revokeShare(share.share_id)}>撤销</button>
                    )}
                  </div>
                ))}
              </div>
            </details>
          </section>

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

          {chain.length > 0 && (
            <>
              <h4 style={{ fontSize: 13, fontWeight: 700, margin: '16px 0 8px' }}>证据链</h4>
              <EvidenceTimeline steps={chain} />
            </>
          )}

          {finding.reproduction?.curl_command && (
            <div style={{ marginTop: 16 }}>
              <h4 style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>复现命令</h4>
              <pre style={{ fontSize: 12, background: 'var(--surface-2)', padding: 12, borderRadius: 8, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                <code>{finding.reproduction.curl_command}</code>
              </pre>
              <p className="settings-hint">原始复现命令仅在登录后的证据中心展示；复制/打印/只读分享都会重新执行服务端或前端脱敏，不直接外发该原始文本。</p>
            </div>
          )}

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
