import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { UploadFixtureRecord } from '../../api/upload-fixtures';
import {
  listUploadScenarios,
  type UploadScenarioRecord,
} from '../../api/ui-upload-scenarios';
import { RunUploadScenarioSelector } from './RunUploadScenarioSelector';
import '../../styles/run-upload-fixtures.css';

export type UploadScenarioRunState = {
  refs: string[];
  loading: boolean;
  error: string;
};

type Props = {
  fixtures: UploadFixtureRecord[];
  selectedRefs: string[];
  loading: boolean;
  error: string;
  onToggle: (bindingRef: string) => void;
  onScenarioStateChange?: (state: UploadScenarioRunState) => void;
  onScenarioSelectionChange?: (scenarioRefs: string[]) => void;
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

function scenarioRef(row: UploadScenarioRecord): string {
  return String(row.scenario_ref || '').trim();
}

export function RunUploadFixtureSelector({
  fixtures,
  selectedRefs,
  loading,
  error,
  onToggle,
  onScenarioStateChange,
  onScenarioSelectionChange,
  onOpenSettings,
  onRefresh,
}: Props) {
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const selected = new Set(selectedRefs);
  const [scenarios, setScenarios] = useState<UploadScenarioRecord[]>([]);
  const [selectedScenarios, setSelectedScenarios] = useState<string[]>([]);
  const [scenarioLoading, setScenarioLoading] = useState(false);
  const [scenarioError, setScenarioError] = useState('');

  const reportScenarioState = useCallback((state: UploadScenarioRunState) => {
    onScenarioStateChange?.(state);
    onScenarioSelectionChange?.(state.refs);
  }, [onScenarioSelectionChange, onScenarioStateChange]);

  const refreshScenarios = useCallback(async () => {
    if (!project) {
      setScenarios([]);
      setSelectedScenarios([]);
      setScenarioLoading(false);
      setScenarioError('');
      reportScenarioState({ refs: [], loading: false, error: '' });
      return;
    }

    setScenarioLoading(true);
    setScenarioError('');
    setSelectedScenarios([]);
    reportScenarioState({ refs: [], loading: true, error: '' });
    try {
      const payload = await listUploadScenarios(project, false);
      const approved = payload.scenarios.filter((row) => (
        row.status === 'active'
        && row.authority === 'approved_copy'
        && Boolean(scenarioRef(row))
      ));
      const refs = approved.map(scenarioRef);
      setScenarios(approved);
      setSelectedScenarios(refs);
      setScenarioError('');
      reportScenarioState({ refs, loading: false, error: '' });
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '上传场景读取失败';
      setScenarios([]);
      setSelectedScenarios([]);
      setScenarioError(message);
      reportScenarioState({ refs: [], loading: false, error: message });
    } finally {
      setScenarioLoading(false);
    }
  }, [project, reportScenarioState]);

  useEffect(() => { void refreshScenarios(); }, [refreshScenarios]);

  return (
    <>
      <RunUploadScenarioSelector
        scenarios={scenarios}
        selected={selectedScenarios}
        loading={scenarioLoading}
        error={scenarioError}
        onRefresh={() => void refreshScenarios()}
      />

      <details className="run-fixture-selector">
        <summary>
          <strong>高级兼容：额外 Fixture</strong>
          <span className="muted">仅在审批场景尚未绑定所需文件时使用</span>
        </summary>

        <div className="run-fixture-selector-head">
          <div>
            <span className="panel-kicker">异常补充入口</span>
            <h3 id="run-fixture-selector-title">额外绑定的审批 Fixture</h3>
            <p>
              正常情况下，系统会从审批场景自动携带 Fixture。这里仅保留给来源合同缺失绑定的兼容情况，
              不再作为每次运行的必选步骤。
            </p>
          </div>
          <strong className={selectedRefs.length ? 'is-positive' : 'is-neutral'}>
            已补充 {selectedRefs.length}/{fixtures.length}
          </strong>
        </div>

        <div className="run-fixture-policy">
          只有已审批、仍在当前项目范围内且文件指纹未漂移的绑定才能进入运行合同；后台会在启动时重新校验。
        </div>

        {error && <p className="settings-inline-feedback" role="alert">✗ {error}</p>}
        {!error && !loading && fixtures.length === 0 && (
          <div className="run-fixture-empty">
            <p>当前没有需要人工补充的 Fixture。系统将按自动绑定结果继续运行。</p>
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
            {loading || scenarioLoading ? '同步中…' : '重新同步审批记录'}
          </button>
          <button type="button" className="btn btn-secondary settings-btn-compact" onClick={onOpenSettings}>
            查看审批与来源配置
          </button>
        </div>
      </details>
    </>
  );
}
