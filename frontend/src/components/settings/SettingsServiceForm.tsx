import { useEffect, useRef, useState } from 'react';
import { DbCredentialPanel, type DbOption } from './DbCredentialPanel';

type AuthType = 'password_login' | 'bearer_token' | 'api_key';

type RoleAccount = {
  role: string;
  username: string;
  password: string;
};

type SettingsServiceFormProps = {
  open: boolean;
  title: string;
  systemName: string;
  moduleName: string;
  serviceName: string;
  endpointRef: string;
  enabled: boolean;
  statusText: string;
  credentialSummary: string;
  authType: AuthType;
  loginApi: string;
  roleAccounts: RoleAccount[];
  bearerToken: string;
  apiKey: string;
  dbOpen: boolean;
  dbType: string;
  dbHost: string;
  dbPort: string;
  dbUser: string;
  dbPass: string;
  dbName: string;
  dbTestLoad: boolean;
  dbTestOk: boolean;
  dbTestMsg: string;
  dbTestHintText: string;
  dbOptions: DbOption[];
  getDbDefaultPort: (type: string) => string;
  onSystemNameChange: (value: string) => void;
  onModuleNameChange: (value: string) => void;
  onServiceNameChange: (value: string) => void;
  onEndpointRefChange: (value: string) => void;
  onEnabledChange: (value: boolean) => void;
  onAuthTypeChange: (value: AuthType) => void;
  onLoginApiChange: (value: string) => void;
  onRoleAccountsChange: (accounts: RoleAccount[]) => void;
  onBearerTokenChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onToggleDbPanel: () => void;
  onDbTypeChange: (value: string) => void;
  onDbHostChange: (value: string) => void;
  onDbPortChange: (value: string) => void;
  onDbUserChange: (value: string) => void;
  onDbPassChange: (value: string) => void;
  onDbNameChange: (value: string) => void;
  onDbTest: () => void;
  onApplyCredentialRef: () => void;
  onSave: () => void;
  onCancel: () => void;
};

type ServiceOnboardingDraft = {
  systemName: string;
  moduleName: string;
  serviceName: string;
  endpointRef: string;
  enabled: boolean;
  authType: AuthType;
  loginApi: string;
  dbType: string;
  dbHost: string;
  dbPort: string;
  dbName: string;
};

const AUTH_TYPE_LABELS: Record<AuthType, string> = {
  password_login: '账号密码',
  bearer_token: 'Bearer Token',
  api_key: 'API Key',
};

const ROLE_OPTIONS = [
  { value: 'viewer', label: '观察者' },
  { value: 'operator', label: '操作员' },
  { value: 'auditor', label: '审计员' },
  { value: '__custom__', label: '自定义…' },
];

function getServiceDraftKey(): string {
  const project = new URLSearchParams(window.location.search).get('project')?.trim() || 'unselected';
  return `qualibug:settings-service-draft:${project}`;
}

function readServiceDraft(key: string): ServiceOnboardingDraft | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ServiceOnboardingDraft>;
    const authType = parsed.authType;
    if (authType !== 'password_login' && authType !== 'bearer_token' && authType !== 'api_key') return null;
    return {
      systemName: typeof parsed.systemName === 'string' ? parsed.systemName : '',
      moduleName: typeof parsed.moduleName === 'string' ? parsed.moduleName : '',
      serviceName: typeof parsed.serviceName === 'string' ? parsed.serviceName : '',
      endpointRef: typeof parsed.endpointRef === 'string' ? parsed.endpointRef : '',
      enabled: typeof parsed.enabled === 'boolean' ? parsed.enabled : true,
      authType,
      loginApi: typeof parsed.loginApi === 'string' ? parsed.loginApi : '/auth/login',
      dbType: typeof parsed.dbType === 'string' ? parsed.dbType : 'postgresql',
      dbHost: typeof parsed.dbHost === 'string' ? parsed.dbHost : '',
      dbPort: typeof parsed.dbPort === 'string' ? parsed.dbPort : '',
      dbName: typeof parsed.dbName === 'string' ? parsed.dbName : '',
    };
  } catch {
    return null;
  }
}

export function SettingsServiceForm({
  open,
  title,
  systemName,
  moduleName,
  serviceName,
  endpointRef,
  enabled,
  statusText,
  credentialSummary,
  authType,
  loginApi,
  roleAccounts,
  bearerToken,
  apiKey,
  dbOpen,
  dbType,
  dbHost,
  dbPort,
  dbUser,
  dbPass,
  dbName,
  dbTestLoad,
  dbTestOk,
  dbTestMsg,
  dbTestHintText,
  dbOptions,
  getDbDefaultPort,
  onSystemNameChange,
  onModuleNameChange,
  onServiceNameChange,
  onEndpointRefChange,
  onEnabledChange,
  onAuthTypeChange,
  onLoginApiChange,
  onRoleAccountsChange,
  onBearerTokenChange,
  onApiKeyChange,
  onToggleDbPanel,
  onDbTypeChange,
  onDbHostChange,
  onDbPortChange,
  onDbUserChange,
  onDbPassChange,
  onDbNameChange,
  onDbTest,
  onApplyCredentialRef,
  onSave,
  onCancel,
}: SettingsServiceFormProps) {
  const [showExtraRoles, setShowExtraRoles] = useState(false);
  const restoredDraftKey = useRef('');
  const isCreateMode = title.includes('新增');

  useEffect(() => {
    if (!open) {
      restoredDraftKey.current = '';
      return;
    }
    if (!isCreateMode) return;

    const key = getServiceDraftKey();
    if (restoredDraftKey.current === key) return;
    restoredDraftKey.current = key;
    const draft = readServiceDraft(key);
    if (!draft) return;

    if (!systemName.trim() && draft.systemName) onSystemNameChange(draft.systemName);
    if (!moduleName.trim() && draft.moduleName) onModuleNameChange(draft.moduleName);
    if (!serviceName.trim() && draft.serviceName) onServiceNameChange(draft.serviceName);
    if (!endpointRef.trim() && draft.endpointRef) onEndpointRefChange(draft.endpointRef);
    onEnabledChange(draft.enabled);
    onAuthTypeChange(draft.authType);
    if ((loginApi === '/auth/login' || !loginApi.trim()) && draft.loginApi) onLoginApiChange(draft.loginApi);
    if (draft.dbType) onDbTypeChange(draft.dbType);
    if (!dbHost.trim() && draft.dbHost) onDbHostChange(draft.dbHost);
    if (draft.dbPort) onDbPortChange(draft.dbPort);
    if (!dbName.trim() && draft.dbName) onDbNameChange(draft.dbName);
  }, [
    open,
    isCreateMode,
    systemName,
    moduleName,
    serviceName,
    endpointRef,
    loginApi,
    dbHost,
    dbName,
    onSystemNameChange,
    onModuleNameChange,
    onServiceNameChange,
    onEndpointRefChange,
    onEnabledChange,
    onAuthTypeChange,
    onLoginApiChange,
    onDbTypeChange,
    onDbHostChange,
    onDbPortChange,
    onDbNameChange,
  ]);

  useEffect(() => {
    if (!open || !isCreateMode) return;

    // onboarding-draft:start — only non-secret setup fields may be persisted here.
    const draft: ServiceOnboardingDraft = {
      systemName,
      moduleName,
      serviceName,
      endpointRef,
      enabled,
      authType,
      loginApi,
      dbType,
      dbHost,
      dbPort,
      dbName,
    };
    // onboarding-draft:end

    try {
      window.sessionStorage.setItem(getServiceDraftKey(), JSON.stringify(draft));
    } catch {
      // Browsers can disable session storage. Draft persistence must never block setup.
    }
  }, [open, isCreateMode, systemName, moduleName, serviceName, endpointRef, enabled, authType, loginApi, dbType, dbHost, dbPort, dbName]);

  useEffect(() => {
    if (open || !statusText.startsWith('✓')) return;
    try {
      window.sessionStorage.removeItem(getServiceDraftKey());
    } catch {
      // Cleanup is best effort only.
    }
  }, [open, statusText]);

  if (!open) return null;

  const adminAccount = roleAccounts.length > 0 && roleAccounts[0].role === 'admin'
    ? roleAccounts[0]
    : { role: 'admin', username: '', password: '' };
  const extraAccounts = roleAccounts.length > 0 && roleAccounts[0].role === 'admin'
    ? roleAccounts.slice(1)
    : [];

  const updateAdmin = (field: 'username' | 'password', value: string) => {
    onRoleAccountsChange([{ ...adminAccount, [field]: value }, ...extraAccounts]);
  };

  const updateExtraRole = (index: number, field: 'role' | 'username' | 'password', value: string) => {
    const next = [...extraAccounts];
    next[index] = { ...next[index], [field]: value };
    onRoleAccountsChange([adminAccount, ...next]);
  };

  const addExtraRole = () => {
    const used = new Set(extraAccounts.map((account) => account.role));
    const nextRole = ROLE_OPTIONS.find((option) => option.value !== '__custom__' && !used.has(option.value));
    onRoleAccountsChange([
      adminAccount,
      ...extraAccounts,
      { role: nextRole?.value || `role_${extraAccounts.length + 1}`, username: '', password: '' },
    ]);
    setShowExtraRoles(true);
  };

  const removeExtraRole = (index: number) => {
    onRoleAccountsChange([adminAccount, ...extraAccounts.filter((_, current) => current !== index)]);
  };

  return (
    <div className="settings-service-form">
      <div className="settings-form-head">
        <div>
          <span className="panel-kicker">最小接入</span>
          <strong>{title}</strong>
        </div>
        <span className="settings-form-head-hint">
          只需提供系统名称、测试地址和可用凭据，其余由后台自动识别
        </span>
      </div>

      {isCreateMode && (
        <p className="settings-hint settings-mt-10">
          非敏感接入草稿会自动保存在当前浏览器会话；账号密码、Token、API Key 和数据库认证信息不会写入草稿。
        </p>
      )}

      <div className="settings-form-grid">
        <div>
          <label className="form-label">系统名称</label>
          <input
            className="form-input"
            value={systemName}
            onChange={(event) => onSystemNameChange(event.target.value)}
            placeholder="例如：制造执行系统"
          />
        </div>
        <div>
          <label className="form-label">测试环境地址</label>
          <input
            className="form-input form-input-mono"
            value={endpointRef}
            onChange={(event) => onEndpointRefChange(event.target.value)}
            placeholder="例如：https://sit.example.internal"
          />
        </div>
      </div>

      <div className="settings-auth-section">
        <div className="settings-section-title">登录凭据</div>
        <p className="settings-auth-hint">
          系统会自动探测登录接口、鉴权流程和会话保持方式。这里只提供无法从页面或接口文档推断的秘密材料。
        </p>

        <div style={{ maxWidth: 360, marginTop: 12 }}>
          <label className="form-label">凭据类型</label>
          <select
            className="form-input"
            value={authType}
            onChange={(event) => onAuthTypeChange(event.target.value as AuthType)}
          >
            {(Object.keys(AUTH_TYPE_LABELS) as AuthType[]).map((type) => (
              <option key={type} value={type}>{AUTH_TYPE_LABELS[type]}</option>
            ))}
          </select>
        </div>

        {authType === 'password_login' && (
          <div className="settings-form-grid" style={{ marginTop: 14 }}>
            <div>
              <label className="form-label">测试账号</label>
              <input
                className="form-input"
                value={adminAccount.username}
                onChange={(event) => updateAdmin('username', event.target.value)}
                placeholder="账号"
              />
            </div>
            <div>
              <label className="form-label">密码</label>
              <input
                className="form-input"
                type="password"
                value={adminAccount.password}
                onChange={(event) => updateAdmin('password', event.target.value)}
                placeholder="密码"
              />
            </div>
          </div>
        )}

        {authType === 'bearer_token' && (
          <div style={{ marginTop: 14 }}>
            <label className="form-label">Bearer Token</label>
            <textarea
              className="form-input form-input-mono"
              rows={3}
              value={bearerToken}
              onChange={(event) => onBearerTokenChange(event.target.value)}
              placeholder="粘贴测试环境 Token"
              style={{ resize: 'vertical', maxWidth: '100%' }}
            />
          </div>
        )}

        {authType === 'api_key' && (
          <div style={{ marginTop: 14, maxWidth: 420 }}>
            <label className="form-label">API Key</label>
            <input
              className="form-input form-input-mono"
              type="password"
              value={apiKey}
              onChange={(event) => onApiKeyChange(event.target.value)}
              placeholder="粘贴测试环境 API Key"
            />
          </div>
        )}
      </div>

      <details className="settings-auth-section">
        <summary><strong>高级选项</strong> <span className="muted">仅在自动识别失败时补充</span></summary>

        <div className="settings-form-grid" style={{ marginTop: 14 }}>
          <div>
            <label className="form-label">模块名称（可选）</label>
            <input
              className="form-input"
              value={moduleName}
              onChange={(event) => onModuleNameChange(event.target.value)}
              placeholder="后台将从资料和页面结构自动识别"
            />
          </div>
          <div>
            <label className="form-label">服务名称（可选）</label>
            <input
              className="form-input"
              value={serviceName}
              onChange={(event) => onServiceNameChange(event.target.value)}
              placeholder="后台将从域名和接口规范自动生成"
            />
          </div>
        </div>

        {authType === 'password_login' && (
          <div style={{ marginTop: 14, maxWidth: 420 }}>
            <label className="form-label">登录接口（可选）</label>
            <input
              className="form-input form-input-mono"
              value={loginApi}
              onChange={(event) => onLoginApiChange(event.target.value)}
              placeholder="自动探测失败时再填写，例如 /auth/login"
            />
          </div>
        )}

        {authType === 'password_login' && (
          <div className="settings-extra-roles" style={{ marginTop: 14 }}>
            <button
              type="button"
              className="settings-extra-roles-toggle"
              onClick={() => setShowExtraRoles((visible) => !visible)}
            >
              <span>{showExtraRoles ? '▾' : '▸'} 权限边界测试账号（可选）</span>
              {!showExtraRoles && extraAccounts.length > 0 && (
                <span className="settings-extra-roles-count">{extraAccounts.length} 个</span>
              )}
            </button>

            {showExtraRoles && (
              <div className="settings-extra-roles-body">
                <p className="settings-auth-hint" style={{ marginBottom: 10 }}>
                  仅当企业希望验证跨角色越权时补充；普通业务验证不需要维护多套账号。
                </p>
                {extraAccounts.map((account, index) => {
                  const customRole = !ROLE_OPTIONS.slice(0, -1).some((option) => option.value === account.role);
                  return (
                    <div key={`${account.role}-${index}`} className="settings-role-row settings-role-row--extra">
                      <div className="settings-role-col settings-role-col--role">
                        {customRole ? (
                          <input
                            className="form-input form-input-sm"
                            value={account.role}
                            onChange={(event) => updateExtraRole(index, 'role', event.target.value)}
                            placeholder="角色名"
                          />
                        ) : (
                          <select
                            className="form-input form-input-sm"
                            value={account.role}
                            onChange={(event) => updateExtraRole(index, 'role', event.target.value === '__custom__' ? '' : event.target.value)}
                          >
                            {ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                          </select>
                        )}
                      </div>
                      <div className="settings-role-col settings-role-col--user">
                        <input
                          className="form-input form-input-sm"
                          value={account.username}
                          onChange={(event) => updateExtraRole(index, 'username', event.target.value)}
                          placeholder="账号"
                        />
                      </div>
                      <div className="settings-role-col settings-role-col--pass">
                        <input
                          className="form-input form-input-sm"
                          type="password"
                          value={account.password}
                          onChange={(event) => updateExtraRole(index, 'password', event.target.value)}
                          placeholder="密码"
                        />
                      </div>
                      <button type="button" className="settings-role-remove-btn" onClick={() => removeExtraRole(index)} title="移除">✕</button>
                    </div>
                  );
                })}
                <button type="button" className="settings-role-add-btn" onClick={addExtraRole}>+ 添加角色</button>
              </div>
            )}
          </div>
        )}

        <DbCredentialPanel
          dbOpen={dbOpen}
          credentialSummary={credentialSummary}
          dbType={dbType}
          dbHost={dbHost}
          dbPort={dbPort}
          dbUser={dbUser}
          dbPass={dbPass}
          dbName={dbName}
          dbTestLoad={dbTestLoad}
          dbTestOk={dbTestOk}
          dbTestMsg={dbTestMsg}
          dbTestHintText={dbTestHintText}
          dbOptions={dbOptions}
          getDbDefaultPort={getDbDefaultPort}
          onToggleOpen={onToggleDbPanel}
          onDbTypeChange={onDbTypeChange}
          onDbHostChange={onDbHostChange}
          onDbPortChange={onDbPortChange}
          onDbUserChange={onDbUserChange}
          onDbPassChange={onDbPassChange}
          onDbNameChange={onDbNameChange}
          onTest={onDbTest}
          onApplyCredentialRef={onApplyCredentialRef}
        />

        <label className="settings-enable-toggle" style={{ marginTop: 14 }}>
          <input type="checkbox" checked={enabled} onChange={(event) => onEnabledChange(event.target.checked)} />
          启用该接入
        </label>
      </details>

      <div className="settings-form-actions">
        <button onClick={onSave} className="btn btn-primary settings-btn-compact">
          保存并自动验证
        </button>
        <button onClick={onCancel} className="btn btn-secondary settings-btn-compact">取消</button>
      </div>
      {statusText && <p className="settings-inline-feedback">{statusText}</p>}
    </div>
  );
}
