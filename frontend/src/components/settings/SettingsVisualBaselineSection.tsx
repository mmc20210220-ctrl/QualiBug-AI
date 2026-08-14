import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  approveVisualBaseline,
  listVisualBaselines,
  registerVisualBaseline,
  revokeVisualBaseline,
  type VisualBaselineInventory,
  type VisualBaselineRecord,
} from '../../api/visual-baselines';
import '../../styles/visual-baselines.css';

type SettingsVisualBaselineSectionProps = {
  project: string;
};

const EMPTY_INVENTORY: VisualBaselineInventory = {
  ok: true,
  schema_version: '',
  project_id: '',
  baselines: [],
  summary: {
    active_count: 0,
    revoked_count: 0,
    source_registered_count: 0,
    approved_copy_count: 0,
  },
  raw_pixels_embedded: false,
};

const MAX_UI_UPLOAD_BYTES = 7_000_000;

function shortHash(value: string): string {
  return value ? `${value.slice(0, 12)}…${value.slice(-8)}` : '—';
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '—';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN');
}

function authorityLabel(record: VisualBaselineRecord): string {
  if (record.authority === 'approved_copy') return '已审批基线';
  if (record.authority === 'source_registered') return '来源登记';
  return record.authority || '未知来源';
}

function actionError(error: unknown): string {
  return error instanceof Error ? error.message : String(error || '操作失败');
}

export function SettingsVisualBaselineSection({ project }: SettingsVisualBaselineSectionProps) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [inventory, setInventory] = useState<VisualBaselineInventory>(EMPTY_INVENTORY);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [includeRevoked, setIncludeRevoked] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [baselineName, setBaselineName] = useState('');
  const [viewportWidth, setViewportWidth] = useState('1280');
  const [viewportHeight, setViewportHeight] = useState('720');
  const [fullPage, setFullPage] = useState(false);
  const [busyKey, setBusyKey] = useState('');
  const [revokeId, setRevokeId] = useState('');
  const [revokeReason, setRevokeReason] = useState('');

  const refresh = useCallback(async (showLoading = true) => {
    if (!project) {
      setInventory(EMPTY_INVENTORY);
      setStatus('');
      return;
    }
    if (showLoading) setLoading(true);
    try {
      const next = await listVisualBaselines(project, { includeRevoked });
      setInventory(next);
      if (next.raw_pixels_embedded) {
        setStatus('加载完成，但后端返回了不符合隐私策略的像素标记，请停止使用并检查服务。');
      }
    } catch (error: unknown) {
      setInventory(EMPTY_INVENTORY);
      setStatus(`加载失败：${actionError(error)}`);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [includeRevoked, project]);

  useEffect(() => {
    let cancelled = false;
    if (!project) {
      setInventory(EMPTY_INVENTORY);
      setStatus('');
      return () => { cancelled = true; };
    }
    setLoading(true);
    setStatus('');
    listVisualBaselines(project, { includeRevoked })
      .then((next) => {
        if (!cancelled) setInventory(next);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setInventory(EMPTY_INVENTORY);
          setStatus(`加载失败：${actionError(error)}`);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [includeRevoked, project]);

  useEffect(() => {
    setFile(null);
    setBaselineName('');
    setRevokeId('');
    setRevokeReason('');
    if (fileRef.current) fileRef.current.value = '';
  }, [project]);

  const rows = useMemo(
    () => [...inventory.baselines].sort((left, right) => {
      const statusOrder = Number(left.status === 'active') - Number(right.status === 'active');
      if (statusOrder !== 0) return -statusOrder;
      const authorityOrder = Number(left.authority === 'approved_copy') - Number(right.authority === 'approved_copy');
      if (authorityOrder !== 0) return -authorityOrder;
      return String(right.created_at_utc || '').localeCompare(String(left.created_at_utc || ''));
    }),
    [inventory.baselines],
  );

  const handleFileChange = (next: File | null) => {
    setStatus('');
    if (!next) {
      setFile(null);
      return;
    }
    if (next.type !== 'image/png' && !next.name.toLowerCase().endsWith('.png')) {
      setFile(null);
      setStatus('请选择 PNG 格式的视觉基线。');
      if (fileRef.current) fileRef.current.value = '';
      return;
    }
    if (next.size > MAX_UI_UPLOAD_BYTES) {
      setFile(null);
      setStatus('当前页面单次上传上限为 7 MB，请压缩 PNG 或通过受控离线登记流程导入。');
      if (fileRef.current) fileRef.current.value = '';
      return;
    }
    setFile(next);
    if (!baselineName.trim()) setBaselineName(next.name.replace(/\.png$/i, ''));
  };

  const handleRegister = async () => {
    if (!project) {
      setStatus('请先选择客户。');
      return;
    }
    if (!file) {
      setStatus('请选择 PNG 视觉基线。');
      return;
    }
    const width = Number(viewportWidth);
    const height = Number(viewportHeight);
    if (!Number.isInteger(width) || width < 240 || width > 7680) {
      setStatus('视口宽度必须是 240–7680 之间的整数。');
      return;
    }
    if (!Number.isInteger(height) || height < 240 || height > 4320) {
      setStatus('视口高度必须是 240–4320 之间的整数。');
      return;
    }
    setBusyKey('register');
    setStatus('正在校验并登记基线…');
    try {
      const record = await registerVisualBaseline({
        project,
        file,
        baselineName: baselineName.trim() || file.name.replace(/\.png$/i, ''),
        viewportWidth: width,
        viewportHeight: height,
        fullPage,
      });
      setStatus(`已登记视觉基线：${record.ref}`);
      setFile(null);
      setBaselineName('');
      if (fileRef.current) fileRef.current.value = '';
      await refresh(false);
    } catch (error: unknown) {
      setStatus(`登记失败：${actionError(error)}`);
    } finally {
      setBusyKey('');
    }
  };

  const handleApprove = async (record: VisualBaselineRecord) => {
    setBusyKey(`approve:${record.baseline_id}`);
    setStatus('正在生成审批副本…');
    try {
      const approved = await approveVisualBaseline(project, record.baseline_id);
      setStatus(`审批完成：${approved.ref}`);
      await refresh(false);
    } catch (error: unknown) {
      setStatus(`审批失败：${actionError(error)}`);
    } finally {
      setBusyKey('');
    }
  };

  const handleRevoke = async (record: VisualBaselineRecord) => {
    const reason = revokeReason.trim();
    const dependentApprovedCount = inventory.baselines.filter((candidate) => (
      candidate.status === 'active'
      && candidate.authority === 'approved_copy'
      && candidate.approved_from_baseline_id === record.baseline_id
    )).length;
    if (revokeId !== record.baseline_id) {
      setRevokeId(record.baseline_id);
      setRevokeReason('');
      setStatus(
        dependentApprovedCount > 0
          ? `该来源基线有 ${dependentApprovedCount} 个活动审批副本；确认后将一并撤销。`
          : '请填写撤销原因后确认。',
      );
      return;
    }
    if (!reason) {
      setStatus('撤销原因不能为空。');
      return;
    }
    setBusyKey(`revoke:${record.baseline_id}`);
    setStatus('正在撤销基线 authority…');
    try {
      const result = await revokeVisualBaseline(
        project,
        record.baseline_id,
        reason,
      );
      setStatus(
        result.cascadeRevokedCount > 0
          ? `已撤销：${record.ref}；同时级联撤销 ${result.cascadeRevokedCount} 个审批副本。`
          : `已撤销：${record.ref}`,
      );
      setRevokeId('');
      setRevokeReason('');
      await refresh(false);
    } catch (error: unknown) {
      setStatus(`撤销失败：${actionError(error)}`);
    } finally {
      setBusyKey('');
    }
  };

  return (
    <div className="section-card visual-baseline-section">
      <div className="settings-card-head visual-baseline-head">
        <div>
          <span className="panel-kicker">UI 正式验证</span>
          <h2>视觉基线治理</h2>
          <p className="visual-baseline-subtitle">
            视觉基线属于可执行测试 authority。系统只使用已登记且身份完全匹配的 PNG，
            不自动更新基线，也不让模型凭主观视觉判断生成正式缺陷。
          </p>
        </div>
        <button
          type="button"
          className="btn btn-secondary settings-btn-compact"
          disabled={!project || loading}
          onClick={() => refresh(true)}
        >
          {loading ? '刷新中…' : '刷新状态'}
        </button>
      </div>

      <div className="visual-baseline-stats">
        <div><span>活动基线</span><strong>{inventory.summary.active_count}</strong></div>
        <div><span>来源登记</span><strong>{inventory.summary.source_registered_count}</strong></div>
        <div><span>已审批</span><strong>{inventory.summary.approved_copy_count}</strong></div>
        <div><span>已撤销</span><strong>{inventory.summary.revoked_count}</strong></div>
      </div>

      <div className="settings-card-note visual-baseline-policy">
        <strong>正式比较固定条件：</strong> Chromium CSS 像素、声明视口、字体加载完成、文档起点、动画禁用。
        动态区域和敏感控件先遮罩；HAR、Trace、原始控制台文本和原始网络 URL 不进入正式证据。
      </div>

      <div className="visual-baseline-form">
        <div className="form-group visual-baseline-file-field">
          <label className="form-label" htmlFor="visual-baseline-file">PNG 基线文件</label>
          <input
            ref={fileRef}
            id="visual-baseline-file"
            className="form-input"
            type="file"
            accept="image/png,.png"
            disabled={!project || busyKey !== ''}
            onChange={(event) => handleFileChange(event.target.files?.[0] || null)}
          />
          <span className="settings-hint">页面上传上限 7 MB；文件原始字节不会写入治理响应。</span>
        </div>

        <div className="form-group">
          <label className="form-label" htmlFor="visual-baseline-name">基线名称</label>
          <input
            id="visual-baseline-name"
            className="form-input"
            value={baselineName}
            disabled={!project || busyKey !== ''}
            placeholder="如：列表页-桌面端"
            onChange={(event) => setBaselineName(event.target.value)}
          />
        </div>

        <div className="form-group">
          <label className="form-label" htmlFor="visual-viewport-width">视口宽度</label>
          <input
            id="visual-viewport-width"
            className="form-input"
            type="number"
            min={240}
            max={7680}
            value={viewportWidth}
            disabled={!project || busyKey !== ''}
            onChange={(event) => setViewportWidth(event.target.value)}
          />
        </div>

        <div className="form-group">
          <label className="form-label" htmlFor="visual-viewport-height">视口高度</label>
          <input
            id="visual-viewport-height"
            className="form-input"
            type="number"
            min={240}
            max={4320}
            value={viewportHeight}
            disabled={!project || busyKey !== ''}
            onChange={(event) => setViewportHeight(event.target.value)}
          />
        </div>

        <label className="visual-baseline-check">
          <input
            type="checkbox"
            checked={fullPage}
            disabled={!project || busyKey !== ''}
            onChange={(event) => setFullPage(event.target.checked)}
          />
          <span>
            <strong>全页截图</strong>
            <small>必须与正式合同中的 full_page 模式完全一致。</small>
          </span>
        </label>

        <div className="visual-baseline-submit">
          <button
            type="button"
            className="btn btn-primary settings-btn-compact"
            disabled={!project || !file || busyKey !== ''}
            onClick={handleRegister}
          >
            {busyKey === 'register' ? '登记中…' : '校验并登记'}
          </button>
          {file && <span className="settings-inline-feedback">{file.name} · {formatBytes(file.size)}</span>}
        </div>
      </div>

      <div className="visual-baseline-list-head">
        <div>
          <h3>项目基线清单</h3>
          <p>只展示治理元数据，不在设置页渲染客户页面像素。</p>
        </div>
        <label className="visual-baseline-history-toggle">
          <input
            type="checkbox"
            checked={includeRevoked}
            disabled={!project || loading}
            onChange={(event) => setIncludeRevoked(event.target.checked)}
          />
          查看撤销历史
        </label>
      </div>

      {!project ? (
        <div className="visual-baseline-empty">请选择客户后管理视觉基线。</div>
      ) : loading ? (
        <div className="visual-baseline-empty">正在读取视觉基线 registry…</div>
      ) : rows.length === 0 ? (
        <div className="visual-baseline-empty">
          尚未登记视觉基线。上传经过评审的 PNG，并填写与正式浏览器合同一致的视口。
        </div>
      ) : (
        <div className="visual-baseline-list">
          {rows.map((record) => {
            const revoked = record.status !== 'active';
            const canApprove = record.status === 'active' && record.authority === 'source_registered';
            const isRevoking = revokeId === record.baseline_id;
            return (
              <article
                key={record.baseline_id}
                className={`visual-baseline-record ${revoked ? 'is-revoked' : ''}`}
              >
                <div className="visual-baseline-record-main">
                  <div className="visual-baseline-record-title">
                    <span className={`visual-baseline-badge authority-${record.authority}`}>
                      {authorityLabel(record)}
                    </span>
                    <span className={`visual-baseline-badge status-${record.status}`}>
                      {record.status === 'active' ? '活动' : '已撤销'}
                    </span>
                    <strong>{record.ref}</strong>
                  </div>
                  <div className="visual-baseline-meta-grid">
                    <span><b>SHA-256</b>{shortHash(record.sha256)}</span>
                    <span><b>视口</b>{record.viewport_width} × {record.viewport_height}</span>
                    <span><b>图像</b>{record.image_width} × {record.image_height}</span>
                    <span><b>模式</b>{record.full_page ? '全页' : '当前视口'}</span>
                    <span><b>大小</b>{formatBytes(record.size_bytes)}</span>
                    <span><b>登记人</b>{record.created_by || '—'}</span>
                    <span><b>登记时间</b>{formatTime(record.created_at_utc)}</span>
                    <span><b>渲染档案</b>{record.renderer_profile || '—'}</span>
                  </div>
                  {record.approved_from_baseline_id && (
                    <p className="visual-baseline-lineage">
                      审批来源：{record.approved_from_baseline_id}
                    </p>
                  )}
                  {revoked && (
                    <p className="visual-baseline-revoked-reason">
                      撤销原因：{record.revocation_reason || '未记录'}
                    </p>
                  )}
                </div>

                {!revoked && (
                  <div className="visual-baseline-actions">
                    {canApprove && (
                      <button
                        type="button"
                        className="btn btn-secondary settings-btn-compact"
                        disabled={busyKey !== ''}
                        onClick={() => handleApprove(record)}
                      >
                        {busyKey === `approve:${record.baseline_id}` ? '审批中…' : '生成审批副本'}
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn btn-danger settings-btn-compact"
                      disabled={busyKey !== ''}
                      onClick={() => handleRevoke(record)}
                    >
                      {busyKey === `revoke:${record.baseline_id}`
                        ? '撤销中…'
                        : isRevoking
                          ? '确认撤销'
                          : '撤销'}
                    </button>
                  </div>
                )}

                {isRevoking && !revoked && (
                  <div className="visual-baseline-revoke-box">
                    <label className="form-label" htmlFor={`revoke-${record.baseline_id}`}>撤销原因</label>
                    <textarea
                      id={`revoke-${record.baseline_id}`}
                      className="form-input settings-textarea"
                      rows={2}
                      maxLength={500}
                      value={revokeReason}
                      disabled={busyKey !== ''}
                      placeholder="说明为何该基线不再具有正式裁决 authority"
                      onChange={(event) => setRevokeReason(event.target.value)}
                    />
                    <button
                      type="button"
                      className="btn btn-ghost settings-btn-compact"
                      disabled={busyKey !== ''}
                      onClick={() => {
                        setRevokeId('');
                        setRevokeReason('');
                        setStatus('');
                      }}
                    >
                      取消
                    </button>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}

      {status && (
        <p className={`settings-inline-feedback visual-baseline-feedback ${status.includes('失败') || status.includes('不能为空') || status.includes('请选择') ? 'is-error' : ''}`} role="status" aria-live="polite">
          {status}
        </p>
      )}
    </div>
  );
}
