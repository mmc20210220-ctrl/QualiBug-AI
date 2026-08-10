import { useEffect, useState } from 'react';
import {
  createEvidenceShare,
  listEvidenceShares,
  revokeEvidenceShare,
  type EvidenceShareMetadata,
} from '../../api/evidence-share';
import type { CollaborativeFindingProjection } from '../../api/finding-collaboration';
import { buildFindingEvidencePackageHtml, buildFindingEvidencePackageText } from '../../lib/finding-evidence-package';
import type { Finding } from '../../types';

type ShareableFinding = Finding & CollaborativeFindingProjection;

type Props = {
  finding: Finding;
  project: string;
};

const TTL_OPTIONS = [
  { value: 60 * 60, label: '1 小时' },
  { value: 24 * 60 * 60, label: '24 小时' },
  { value: 3 * 24 * 60 * 60, label: '3 天' },
  { value: 7 * 24 * 60 * 60, label: '7 天' },
];

export function EvidenceDistributionTools({ finding, project }: Props) {
  const [exportStatus, setExportStatus] = useState('');
  const [shareStatus, setShareStatus] = useState('');
  const [shareTtl, setShareTtl] = useState(24 * 60 * 60);
  const [shares, setShares] = useState<EvidenceShareMetadata[]>([]);
  const [sharesLoading, setSharesLoading] = useState(false);
  const [shareSaving, setShareSaving] = useState(false);
  const [generatedShareUrl, setGeneratedShareUrl] = useState('');
  const persistenceId = (finding as ShareableFinding).finding_persistence_id || '';

  useEffect(() => {
    setGeneratedShareUrl('');
    setShareStatus('');
    if (!project || !persistenceId) {
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
  }, [finding.id, persistenceId, project]);

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
    <details className="settings-auth-section mt-3" aria-label="证据导出与安全分发工具">
      <summary>
        <strong>证据工具</strong>
        <span className="muted">复制 / PDF / 临时只读分享</span>
      </summary>

      <div className="mt-3">
        <p className="settings-card-note">
          这些是证据核对后的分发工具，不参与问题是否成立或是否修复的判断。复制/打印只生成本地脱敏副本；公开链接只能读取创建当刻冻结的脱敏快照，不能访问项目后台或原始凭据。
        </p>

        <div className="settings-actions mt-3">
          <button className="btn btn-secondary btn-sm" type="button" onClick={() => void copyEvidencePackage()}>
            复制脱敏证据包
          </button>
          <button className="btn btn-secondary btn-sm" type="button" onClick={openPrintableEvidencePackage}>
            打印 / PDF
          </button>
        </div>
        {exportStatus && <div className="settings-inline-feedback" role="status">{exportStatus}</div>}

        <section className="card mt-3" aria-label="只读证据分享">
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
                className="btn btn-secondary btn-sm"
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

        <p className="settings-hint mt-3">
          原始复现命令只用于登录后的证据核对；复制/打印/只读分享都会重新执行服务端或前端脱敏，不直接外发该原始文本。
        </p>
      </div>
    </details>
  );
}

export default EvidenceDistributionTools;
