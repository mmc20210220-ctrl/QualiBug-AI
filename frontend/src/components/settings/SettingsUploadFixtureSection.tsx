import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  approveUploadFixture,
  listUploadFixtures,
  revokeUploadFixture,
  uploadFixtureFile,
  type UploadFixtureRecord,
} from '../../api/upload-fixtures';
import '../../styles/upload-fixture-settings.css';

function formatBytes(value?: number): string {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function shortHash(value?: string): string {
  const text = String(value || '');
  return text ? `${text.slice(0, 12)}…${text.slice(-8)}` : '—';
}

function authorityLabel(value?: string): string {
  if (value === 'approved_copy') return '已审批可执行';
  if (value === 'source_registered') return '候选待审批';
  return value || '未知';
}

export function SettingsUploadFixtureSection() {
  const [params] = useSearchParams();
  const projectId = params.get('project')?.trim() || '';
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fixtureName, setFixtureName] = useState('');
  const [fixtures, setFixtures] = useState<UploadFixtureRecord[]>([]);
  const [includeRevoked, setIncludeRevoked] = useState(false);
  const [revocationReason, setRevocationReason] = useState('测试文件已失效或被新版本替代');
  const [busyId, setBusyId] = useState('');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');

  const refresh = useCallback(async () => {
    if (!projectId) {
      setFixtures([]);
      return;
    }
    setLoading(true);
    try {
      const payload = await listUploadFixtures(projectId, includeRevoked);
      setFixtures(payload.fixtures);
      setStatus('');
    } catch (error) {
      setFixtures([]);
      setStatus(error instanceof Error ? `✗ ${error.message}` : '✗ 无法读取测试文件登记表');
    } finally {
      setLoading(false);
    }
  }, [includeRevoked, projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const summary = useMemo(() => ({
    active: fixtures.filter((item) => item.status === 'active').length,
    candidates: fixtures.filter((item) => item.status === 'active' && item.authority === 'source_registered').length,
    approved: fixtures.filter((item) => item.status === 'active' && item.authority === 'approved_copy').length,
  }), [fixtures]);

  const upload = async () => {
    if (!projectId) {
      setStatus('✗ 请先选择客户项目');
      return;
    }
    if (!file) {
      setStatus('✗ 请选择测试文件');
      return;
    }
    setBusyId('upload');
    setStatus('上传并登记中…');
    try {
      const result = await uploadFixtureFile(projectId, file, fixtureName);
      setStatus(`✓ ${result.status === 'DUPLICATE_ACTIVE' ? '已存在相同候选文件' : '候选文件已登记，仍需审批后才能执行'}`);
      setFile(null);
      setFixtureName('');
      if (inputRef.current) inputRef.current.value = '';
      await refresh();
    } catch (error) {
      setStatus(error instanceof Error ? `✗ ${error.message}` : '✗ 上传失败');
    } finally {
      setBusyId('');
    }
  };

  const approve = async (fixture: UploadFixtureRecord) => {
    if (!fixture.fixture_id) return;
    setBusyId(fixture.fixture_id);
    setStatus('审批中…');
    try {
      const result = await approveUploadFixture(projectId, fixture.fixture_id);
      const binding = result.fixture?.binding_ref || '';
      setStatus(binding ? `✓ 已审批，运行时 binding_ref：${binding}` : '✓ 已审批');
      await refresh();
    } catch (error) {
      setStatus(error instanceof Error ? `✗ ${error.message}` : '✗ 审批失败');
    } finally {
      setBusyId('');
    }
  };

  const revoke = async (fixture: UploadFixtureRecord) => {
    if (!fixture.fixture_id) return;
    setBusyId(fixture.fixture_id);
    setStatus('撤销中…');
    try {
      await revokeUploadFixture(projectId, fixture.fixture_id, revocationReason);
      setStatus(
        fixture.authority === 'source_registered'
          ? '✓ 候选及其审批副本已级联撤销，后续运行不能继续绑定'
          : '✓ 审批副本已撤销，后续运行不能继续绑定',
      );
      await refresh();
    } catch (error) {
      setStatus(error instanceof Error ? `✗ ${error.message}` : '✗ 撤销失败');
    } finally {
      setBusyId('');
    }
  };

  const copyBinding = async (fixture: UploadFixtureRecord) => {
    const binding = fixture.binding_ref?.trim();
    if (!binding) return;
    const snippet = JSON.stringify({
      ui_upload_fixture_ids: [binding],
      browser_plan_step: {
        action: 'set_input_files',
        phase: 'treatment',
        selector: 'input[type=file]',
        file_refs: [binding],
      },
    }, null, 2);
    try {
      await navigator.clipboard.writeText(snippet);
      setStatus('✓ 已复制扫描绑定和 set_input_files 片段');
    } catch {
      setStatus(`请手动复制 binding_ref：${binding}`);
    }
  };

  return (
    <div className="section-card browser-matrix-section upload-fixture-section">
      <div className="settings-card-head browser-matrix-head">
        <div>
          <span className="panel-kicker">UI 测试文件治理</span>
          <h2>上传 Fixture 登记与审批</h2>
          <p>文件先登记为候选，审批副本生成后才可进入受控浏览器执行。</p>
        </div>
        <strong className={summary.approved > 0 ? 'is-positive' : 'is-neutral'}>
          可执行 {summary.approved} 个
        </strong>
      </div>

      <div className="browser-matrix-policy">
        浏览器直接上传上限为 10MiB，不使用 Base64。系统保存不可变 SHA-256 身份；撤销候选会同时撤销由它产生的审批副本，历史字节只为审计保留。
      </div>

      <div className="upload-fixture-form">
        <label className="form-field">
          <span>选择测试文件</span>
          <input
            ref={inputRef}
            className="form-input"
            type="file"
            disabled={!projectId || busyId === 'upload'}
            onChange={(event) => {
              const next = event.target.files?.[0] || null;
              setFile(next);
              if (next && !fixtureName.trim()) setFixtureName(next.name.replace(/\.[^.]+$/, ''));
              setStatus('');
            }}
          />
        </label>
        <label className="form-field">
          <span>Fixture 名称</span>
          <input
            className="form-input"
            value={fixtureName}
            placeholder="例如：客户批量导入_合法样本"
            disabled={!projectId || busyId === 'upload'}
            onChange={(event) => setFixtureName(event.target.value)}
          />
        </label>
        <button
          type="button"
          className="btn btn-primary settings-btn-compact"
          disabled={!projectId || !file || busyId === 'upload'}
          onClick={() => void upload()}
        >
          {busyId === 'upload' ? '登记中…' : '上传并登记候选'}
        </button>
      </div>

      <div className="upload-fixture-toolbar">
        <div>
          <strong>登记表</strong>
          <span> 活跃 {summary.active} · 待审批 {summary.candidates} · 可执行 {summary.approved}</span>
        </div>
        <label className="upload-fixture-toggle">
          <input
            type="checkbox"
            checked={includeRevoked}
            onChange={(event) => setIncludeRevoked(event.target.checked)}
          />
          显示已撤销
        </label>
        <button type="button" className="btn btn-secondary settings-btn-compact" onClick={() => void refresh()} disabled={loading}>
          {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <label className="form-field upload-fixture-reason">
        <span>撤销原因</span>
        <input
          className="form-input"
          value={revocationReason}
          onChange={(event) => setRevocationReason(event.target.value)}
        />
      </label>

      <div className="upload-fixture-list" aria-live="polite">
        {!projectId && <p className="settings-inline-feedback">请先选择客户项目。</p>}
        {projectId && !loading && fixtures.length === 0 && (
          <p className="settings-inline-feedback">尚未登记 UI 上传测试文件。</p>
        )}
        {fixtures.map((fixture) => {
          const active = fixture.status === 'active';
          const candidate = fixture.authority === 'source_registered';
          return (
            <article key={fixture.fixture_id} className={`upload-fixture-row ${active ? '' : 'is-revoked'}`}>
              <div className="upload-fixture-main">
                <div className="upload-fixture-title">
                  <strong>{fixture.fixture_name || fixture.fixture_id}</strong>
                  <span>{authorityLabel(fixture.authority)}</span>
                  {!active && <span>已撤销</span>}
                </div>
                <small>
                  {formatBytes(fixture.size_bytes)} · {fixture.content_type || 'application/octet-stream'} · SHA-256 {shortHash(fixture.sha256)}
                </small>
                {fixture.binding_ref && <code>{fixture.binding_ref}</code>}
                {!active && fixture.revocation_reason && <em>撤销原因：{fixture.revocation_reason}</em>}
              </div>
              <div className="upload-fixture-actions">
                {active && candidate && (
                  <button
                    type="button"
                    className="btn btn-primary settings-btn-compact"
                    disabled={busyId === fixture.fixture_id}
                    onClick={() => void approve(fixture)}
                  >
                    审批为可执行
                  </button>
                )}
                {active && fixture.authority === 'approved_copy' && fixture.binding_ref && (
                  <button type="button" className="btn btn-secondary settings-btn-compact" onClick={() => void copyBinding(fixture)}>
                    复制绑定片段
                  </button>
                )}
                {active && (
                  <button
                    type="button"
                    className="btn btn-secondary settings-btn-compact"
                    disabled={busyId === fixture.fixture_id || !revocationReason.trim()}
                    onClick={() => void revoke(fixture)}
                  >
                    撤销
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>
      {status && <p className="settings-inline-feedback" role="status">{status}</p>}
    </div>
  );
}
