import type { UploadFixtureRecord } from '../../api/upload-fixtures';
import '../../styles/run-upload-fixtures.css';

type Props = {
  fixtures: UploadFixtureRecord[];
  selectedRefs: string[];
  loading: boolean;
  error: string;
  onToggle: (bindingRef: string) => void;
  onOpenSettings: () => void;
  onRefresh: () => void;
};

function formatBytes(value?: number): string {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function shortHash(value?: string): string {
  const text = String(value || '');
  return text ? `${text.slice(0, 10)}…${text.slice(-8)}` : '—';
}

export function RunUploadFixtureSelector({
  fixtures,
  selectedRefs,
  loading,
  error,
  onToggle,
  onOpenSettings,
  onRefresh,
}: Props) {
  const selected = new Set(selectedRefs);

  return (
    <section className="run-fixture-selector" aria-labelledby="run-fixture-selector-title">
      <div className="run-fixture-selector-head">
        <div>
          <span className="panel-kicker">受控上传输入</span>
          <h3 id="run-fixture-selector-title">本次运行使用的审批 Fixture</h3>
          <p>
            这里只列出活动审批副本。勾选只会把 binding_ref 放入本次扫描合同；真正上传仍须来源 UI 合同声明
            set_input_files、目标 selector、断言与 cleanup。
          </p>
        </div>
        <strong className={selectedRefs.length ? 'is-positive' : 'is-neutral'}>
          已选 {selectedRefs.length}/{fixtures.length}
        </strong>
      </div>

      <div className="run-fixture-policy">
        不会自动选择文件。扫描启动时后端会重新校验项目范围、活动审批状态、文件大小和 SHA-256；撤销或漂移的绑定会阻断运行。
      </div>

      {error && <p className="settings-inline-feedback" role="alert">✗ {error}</p>}
      {!error && !loading && fixtures.length === 0 && (
        <div className="run-fixture-empty">
          <p>当前项目没有可执行的上传 Fixture。</p>
          <button type="button" className="btn btn-secondary settings-btn-compact" onClick={onOpenSettings}>
            前往设置登记与审批
          </button>
        </div>
      )}

      {fixtures.length > 0 && (
        <div className="run-fixture-grid">
          {fixtures.map((fixture) => {
            const bindingRef = String(fixture.binding_ref || '').trim();
            const checked = selected.has(bindingRef);
            return (
              <label key={fixture.fixture_id} className={`run-fixture-option ${checked ? 'is-selected' : ''}`}>
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!bindingRef || loading}
                  onChange={() => onToggle(bindingRef)}
                />
                <span>
                  <strong>{fixture.fixture_name || fixture.fixture_id}</strong>
                  <small>
                    {formatBytes(fixture.size_bytes)} · {fixture.content_type || 'application/octet-stream'} · SHA-256 {shortHash(fixture.sha256)}
                  </small>
                  <code>{bindingRef}</code>
                </span>
              </label>
            );
          })}
        </div>
      )}

      <div className="run-fixture-actions">
        <button type="button" className="btn btn-secondary settings-btn-compact" onClick={onRefresh} disabled={loading}>
          {loading ? '刷新中…' : '刷新审批记录'}
        </button>
        <button type="button" className="btn btn-secondary settings-btn-compact" onClick={onOpenSettings}>
          管理 Fixture
        </button>
      </div>
    </section>
  );
}
