type WorkspaceOption = {
  id: string;
  label: string;
};

type SettingsCustomerSectionProps = {
  workspaceLabel: string;
  workspacesCount: number;
  project: string;
  workspaceOptions: WorkspaceOption[];
  importId: string;
  wsStatus: string;
  workspaceLoadFailed?: boolean;
  onWorkspaceChange: (value: string) => void;
  onRefresh: () => void;
  onImportIdChange: (value: string) => void;
  onCreateWorkspace: () => void;
};

export function SettingsCustomerSection({
  workspaceLabel,
  workspacesCount,
  project,
  workspaceOptions,
  importId,
  wsStatus,
  workspaceLoadFailed = false,
  onWorkspaceChange,
  onRefresh,
  onImportIdChange,
  onCreateWorkspace,
}: SettingsCustomerSectionProps) {
  return (
    <div className="section-card">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">客户治理</span>
          <h2>客户</h2>
        </div>
      </div>
      <div className="settings-card-note">选择当前服务对象，或直接创建新的客户。</div>
      <div className="settings-mini-stats">
        <div className="settings-mini-stat">
          <span>当前客户</span>
          <strong>{workspaceLabel}</strong>
        </div>
        <div className="settings-mini-stat">
          <span>客户数量</span>
          <strong>{workspacesCount}</strong>
        </div>
      </div>
      <div className="settings-compact-row">
        <div className="form-group settings-flex-grow">
          <label className="form-label">客户名称</label>
          <select className="form-input" value={project} onChange={(e) => onWorkspaceChange(e.target.value)}>
            <option value="">{workspacesCount ? '请选择客户' : (workspaceLoadFailed ? '客户列表加载失败' : '暂无客户')}</option>
            {workspaceOptions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </div>
        <button onClick={onRefresh} className="btn btn-secondary settings-btn-compact">刷新</button>
      </div>
      <div className="settings-compact-row settings-mt-10">
        <div className="form-group settings-flex-grow">
          <label className="form-label">新客户名称</label>
          <input className="form-input" value={importId} onChange={(e) => onImportIdChange(e.target.value)} placeholder="公司名称" />
        </div>
        <button onClick={onCreateWorkspace} className="btn btn-primary settings-btn-compact">新建客户</button>
      </div>
      {wsStatus && <p className="settings-inline-feedback">{wsStatus}</p>}
    </div>
  );
}
