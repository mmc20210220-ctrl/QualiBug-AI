import type { UploadScenarioRecord } from '../../api/ui-upload-scenarios';

type RunUploadScenarioSelectorProps = {
  scenarios: UploadScenarioRecord[];
  selected: string[];
  loading: boolean;
  error: string;
  onRefresh: () => void;
};

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function RunUploadScenarioSelector({
  scenarios,
  selected,
  loading,
  error,
  onRefresh,
}: RunUploadScenarioSelectorProps) {
  return (
    <section className="card mb-4">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">后台自动编排</span>
          <h2>已批准的 UI 场景自动纳入本次验证</h2>
          <p>
            系统自动读取当前项目中仍然有效的审批场景，并携带其来源版本、Fixture、断言和 cleanup 合同。
            用户不需要逐项选择或重复确认。
          </p>
        </div>
        <button type="button" className="btn btn-secondary settings-btn-compact" onClick={onRefresh} disabled={loading}>
          {loading ? '同步中…' : '重新同步'}
        </button>
      </div>

      {error && <p className="settings-inline-feedback">✗ {error}</p>}

      {!error && loading && <p className="muted">正在同步活动审批场景…</p>}

      {!error && !loading && scenarios.length === 0 && (
        <p className="muted">
          当前没有可自动执行的审批上传场景。普通接口、页面和只读验证仍会继续；只有确实需要上传文件的场景会被跳过并记录缺口。
        </p>
      )}

      {!error && !loading && scenarios.length > 0 && (
        <>
          <div className="browser-matrix-profile-grid">
            {scenarios.map((scenario) => {
              const scenarioRef = text(scenario.scenario_ref) || scenario.scenario_id;
              const active = selected.includes(scenarioRef);
              return (
                <article key={scenario.scenario_id} className={`browser-matrix-profile ${active ? 'is-selected' : ''}`}>
                  <span>
                    <strong>{scenario.title || scenario.scenario_id}</strong>
                    <small>
                      系统已自动绑定 · 来源 {scenario.source_id || 'unknown'} · Fixture {scenario.fixture_binding_refs?.length || 0} 个
                    </small>
                    <em>{scenarioRef}</em>
                  </span>
                </article>
              );
            })}
          </div>
          <p className="muted">
            已自动纳入 {selected.length}/{scenarios.length} 个活动审批场景。目标环境、来源、Fixture 或 cleanup 不满足时，后台仍会安全阻断并说明原因。
          </p>
        </>
      )}
    </section>
  );
}
