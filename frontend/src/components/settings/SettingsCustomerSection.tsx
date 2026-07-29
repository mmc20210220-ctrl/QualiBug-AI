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
          <span className="panel-kicker">项目上下文</span>
          <h2>选择客户项目</h2>
        </div>
      </div>
      <div className="settings-card-note">
        这里只决定本次查看和验证属于哪个客户。项目内部结构、测试范围和运行策略不需要在这里维护。
      </div>
      <div className="settings-compact-row">
        <div className="form-group settings-flex-grow">
          <label className="form-label">当前客户</label>
          <select className="form-input" value={project} onChange={(e) => onWorkspaceChange(e.target.value)}>
            <option value="">{workspacesCount ? '请选择客户' : (workspaceLoadFailed ? '客户列表加载失败' : '暂无客户')}</option>
            {workspaceOptions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </div>
        <button onClick={onRefresh} className="btn btn-secondary settings-btn-compact">重新同步</button>
      </div>
      <p className="settings-hint">当前：{workspaceLabel}</p>

      <details className="settings-auth-section settings-mt-10">
        <summary><strong>创建新客户项目</strong> <span className="muted">仅首次接入新客户时使用</span></summary>
        <div className="settings-compact-row settings-mt-10">
          <div className="form-group settings-flex-grow">
            <label className="form-label">公司名称</label>
            <input className="form-input" value={importId} onChange={(e) => onImportIdChange(e.target.value)} placeholder="输入公司名称" />
          </div>
          <button onClick={onCreateWorkspace} className="btn btn-primary settings-btn-compact">创建并切换</button>
        </div>
      </details>
      {wsStatus && <p className="settings-inline-feedback">{wsStatus}</p>}
    </div>
  );
}
