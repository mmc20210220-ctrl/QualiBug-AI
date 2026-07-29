import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { UploadFixtureRecord } from '../../api/upload-fixtures';
import {
  listUploadScenarios,
  type UploadScenarioRecord,
} from '../../api/ui-upload-scenarios';
import { RunUploadScenarioSelector } from './RunUploadScenarioSelector';
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

function uploadScenarioDraftKey(project: string): string {
  return `qualibug.run.ui-upload-scenarios.${project.trim()}`;
}

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

function scenarioRef(row: UploadScenarioRecord): string {
  return String(row.scenario_ref || row.scenario_id || '').trim();
}

function readScenarioDraft(project: string): string[] {
  if (!project) return [];
  try {
    const value = JSON.parse(localStorage.getItem(uploadScenarioDraftKey(project)) || '[]') as unknown;
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
      : [];
  } catch {
    return [];
  }
}

function writeScenarioDraft(project: string, refs: string[]): void {
  if (!project) return;
  localStorage.setItem(uploadScenarioDraftKey(project), JSON.stringify(refs));
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
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const selected = new Set(selectedRefs);
  const [scenarios, setScenarios] = useState<UploadScenarioRecord[]>([]);
  const [selectedScenarios, setSelectedScenarios] = useState<string[]>(() => readScenarioDraft(project));
  const [scenarioLoading, setScenarioLoading] = useState(false);
  const [scenarioError, setScenarioError] = useState('');

  const refreshScenarios = useCallback(async () => {
    if (!project) {
      setScenarios([]);
      setSelectedScenarios([]);
      return;
    }
    setScenarioLoading(true);
    try {
      const payload = await listUploadScenarios(project, false);
      const approved = payload.scenarios.filter((row) => (
        row.status === 'active'
        && row.authority === 'approved_copy'
        && Boolean(scenarioRef(row))
      ));
      const activeRefs = new Set(approved.map(scenarioRef));
      const nextSelected = readScenarioDraft(project).filter((ref) => activeRefs.has(ref));
      setScenarios(approved);
      setSelectedScenarios(nextSelected);
      writeScenarioDraft(project, nextSelected);
      setScenarioError('');
    } catch (caught) {
      setScenarios([]);
      setSelectedScenarios([]);
      setScenarioError(caught instanceof Error ? caught.message : '上传场景读取失败');
    } finally {
      setScenarioLoading(false);
    }
  }, [project]);

  useEffect(() => { void refreshScenarios(); }, [refreshScenarios]);

  const toggleScenario = useCallback((ref: string) => {
    setSelectedScenarios((current) => {
      const next = current.includes(ref)
        ? current.filter((item) => item !== ref)
        : [...current, ref];
      writeScenarioDraft(project, next);
      return next;
    });
  }, [project]);

  return (
    <>
      <RunUploadScenarioSelector
        scenarios={scenarios}
        selected={selectedScenarios}
        loading={scenarioLoading}
        error={scenarioError}
        onToggle={toggleScenario}
        onRefresh={() => void refreshScenarios()}
      />
      <section className="run-fixture-selector" aria-labelledby="run-fixture-selector-title">
        <div className="run-fixture-selector-head">
          <div>
            <span className="panel-kicker">受控上传输入</span>
            <h3 id="run-fixture-selector-title">额外绑定的审批 Fixture</h3>
            <p>
              审批上传场景会自动携带自己的 Fixture。这里只用于已有来源合同需要额外文件、但尚未登记为治理场景的兼容路径。
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
          <button
            type="button"
            className="btn btn-secondary settings-btn-compact"
            onClick={() => {
              onRefresh();
              void refreshScenarios();
            }}
            disabled={loading || scenarioLoading}
          >
            {loading || scenarioLoading ? '刷新中…' : '刷新审批记录'}
          </button>
          <button type="button" className="btn btn-secondary settings-btn-compact" onClick={onOpenSettings}>
            管理 Fixture 与场景
          </button>
        </div>
      </section>
    </>
  );
}
