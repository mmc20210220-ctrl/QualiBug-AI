import type { UploadScenarioRecord } from '../../api/ui-upload-scenarios';

type RunUploadScenarioSelectorProps = {
  scenarios: UploadScenarioRecord[];
  selected: string[];
  loading: boolean;
  error: string;
  onToggle: (scenarioRef: string) => void;
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
  onToggle,
  onRefresh,
}: RunUploadScenarioSelectorProps) {
  return (
    <section className="card mb-4">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">来源 UI 上传场景</span>
          <h2>选择本次运行场景</h2>
          <p>只显示活动审批场景。场景已冻结来源版本、Fixture、断言和 cleanup 合同。</p>
        </div>
        <button type="button" className="btn btn-secondary settings-btn-compact" onClick={onRefresh} disabled={loading}>
          {loading ? '刷新中…' : '刷新场景'}
        </button>
      </div>
      {error && <p className="settings-inline-feedback">✗ {error}</p>}
      {!loading && scenarios.length === 0 && (
        <p className="muted">当前没有可运行的审批上传场景。请先到项目设置完成场景登记与审批。</p>
      )}
      <div className="browser-matrix-profile-grid">
        {scenarios.map((scenario) => {
          const scenarioRef = text(scenario.scenario_ref) || scenario.scenario_id;
          return (
            <label key={scenario.scenario_id} className="browser-matrix-profile">
              <input
                type="checkbox"
                checked={selected.includes(scenarioRef)}
                onChange={() => onToggle(scenarioRef)}
              />
              <span>
                <strong>{scenario.title || scenario.scenario_id}</strong>
                <small>
                  来源 {scenario.source_id || 'unknown'} · Fixture {scenario.fixture_binding_refs?.length || 0} 个
                </small>
                <em>{scenarioRef}</em>
              </span>
            </label>
          );
        })}
      </div>
      <p className="muted">
        已选 {selected.length} 个。选择后本次扫描会请求 approved_sandbox_write；目标环境、来源、Fixture 或 cleanup 不满足时仍会阻断。
      </p>
    </section>
  );
}
