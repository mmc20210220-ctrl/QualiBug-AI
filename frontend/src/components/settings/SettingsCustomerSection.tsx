import { useProjectNavigation } from '../../lib/project-navigation';

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
  const { navigateToProjectPath } = useProjectNavigation();

  return (
    <div className="section-card">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">项目接入</span>
          <h2>客户项目</h2>
        </div>
      </div>
      <div className="settings-card-note">
        这里仅负责选择或创建客户工作区。企业资料统一在“企业资料”页面接入和维护：在线资料源作为主入口持续同步，文件上传只用于补充在线来源没有覆盖的资料。
      </div>

      <div className="settings-compact-row">
        <div className="form-group settings-flex-grow">
          <label className="form-label">当前客户</label>
          <select className="form-input" value={project} onChange={(event) => onWorkspaceChange(event.target.value)}>
            <option value="">{workspacesCount ? '请选择客户' : (workspaceLoadFailed ? '客户列表加载失败' : '暂无客户')}</option>
            {workspaceOptions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </div>
        <button onClick={onRefresh} className="btn btn-secondary settings-btn-compact">重新同步</button>
      </div>
      <p className="settings-hint">当前：{workspaceLabel}</p>

      {project && (
        <section className="section-card settings-mt-10" aria-label="企业资料唯一入口">
          <div className="settings-card-head">
            <div>
              <span className="panel-kicker">企业资料</span>
              <h3>优先连接企业在线资料</h3>
              <p className="settings-card-sub">在线文档、知识库等资料源统一从资料中心连接并保持更新；PRD、接口文档、历史缺陷、数据库说明或设计稿缺失时，再用文件上传补充。Settings 不维护第二套资料流程。</p>
            </div>
          </div>
          <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/materials', project)}>
            连接企业资料
          </button>
        </section>
      )}

      <details className="settings-auth-section settings-mt-10">
        <summary><strong>创建新客户项目</strong> <span className="muted">仅首次接入新客户时使用</span></summary>
        <div className="settings-compact-row settings-mt-10">
          <div className="form-group settings-flex-grow">
            <label className="form-label">公司名称</label>
            <input className="form-input" value={importId} onChange={(event) => onImportIdChange(event.target.value)} placeholder="输入公司名称" />
          </div>
          <button onClick={onCreateWorkspace} className="btn btn-primary settings-btn-compact">创建并切换</button>
        </div>
      </details>
      {wsStatus && <p className="settings-inline-feedback" role="status">{wsStatus}</p>}
    </div>
  );
}
