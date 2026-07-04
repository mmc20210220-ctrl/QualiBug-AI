import { DbCredentialPanel, type DbOption } from './DbCredentialPanel';
import { useState } from 'react';

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
  /* ── API Auth ── */
  authType: AuthType;
  loginApi: string;
  roleAccounts: RoleAccount[];
  bearerToken: string;
  apiKey: string;
  /* ── DB ── */
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
  /* ── Auth handlers ── */
  onAuthTypeChange: (value: AuthType) => void;
  onLoginApiChange: (value: string) => void;
  onRoleAccountsChange: (accounts: RoleAccount[]) => void;
  onBearerTokenChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  /* ── DB handlers ── */
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

const AUTH_TYPE_LABELS: Record<AuthType, string> = {
  password_login: '🔐 密码自动登录',
  bearer_token: '🎫 Bearer Token',
  api_key: '🔑 API Key',
};

const ROLE_OPTIONS = [
  { value: 'viewer', label: '观察者' },
  { value: 'operator', label: '操作员' },
  { value: 'auditor', label: '审计员' },
  { value: '__custom__', label: '自定义…' },
];

export function SettingsServiceForm({
  open, title,
  systemName, moduleName, serviceName, endpointRef, enabled, statusText, credentialSummary,
  authType, loginApi, roleAccounts, bearerToken, apiKey,
  dbOpen, dbType, dbHost, dbPort, dbUser, dbPass, dbName,
  dbTestLoad, dbTestOk, dbTestMsg, dbTestHintText, dbOptions, getDbDefaultPort,
  onSystemNameChange, onModuleNameChange, onServiceNameChange, onEndpointRefChange,
  onEnabledChange,
  onAuthTypeChange, onLoginApiChange, onRoleAccountsChange,
  onBearerTokenChange, onApiKeyChange,
  onToggleDbPanel, onDbTypeChange, onDbHostChange, onDbPortChange,
  onDbUserChange, onDbPassChange, onDbNameChange, onDbTest, onApplyCredentialRef,
  onSave, onCancel,
}: SettingsServiceFormProps) {
  const hasEndpoint = endpointRef.trim().length > 0;
  const [showExtraRoles, setShowExtraRoles] = useState(false);

  if (!open) return null;

  // Derive admin account (always first)
  const adminAccount = roleAccounts.length > 0 && roleAccounts[0].role === 'admin'
    ? roleAccounts[0]
    : { role: 'admin', username: '', password: '' };
  // Extra roles = all but admin
  const extraAccounts = roleAccounts.length > 0 && roleAccounts[0].role === 'admin'
    ? roleAccounts.slice(1)
    : [];

  const updateAdmin = (field: 'username'|'password', value: string) => {
    const next = [{ ...adminAccount, [field]: value }, ...extraAccounts];
    onRoleAccountsChange(next);
  };

  const updateExtraRole = (index: number, field: 'role'|'username'|'password', value: string) => {
    const next = [...extraAccounts];
    next[index] = { ...next[index], [field]: value };
    onRoleAccountsChange([adminAccount, ...next]);
  };

  const addExtraRole = () => {
    const used = new Set(extraAccounts.map(a => a.role));
    const next = ROLE_OPTIONS.find(o => !used.has(o.value) && o.value !== '__custom__');
    const roleName = next ? next.value : `role_${extraAccounts.length + 1}`;
    onRoleAccountsChange([adminAccount, ...extraAccounts, { role: roleName, username: '', password: '' }]);
    setShowExtraRoles(true);
  };

  const removeExtraRole = (index: number) => {
    onRoleAccountsChange([adminAccount, ...extraAccounts.filter((_, i) => i !== index)]);
  };

  return (
    <div className="settings-service-form">
      <div className="settings-form-head">
        <div>
          <span className="panel-kicker">服务配置</span>
          <strong>{title}</strong>
        </div>
        <span className="settings-form-head-hint">
          {hasEndpoint ? '已填写测试地址 · 可继续配置 API 认证' : '填写测试地址后配置 API 认证'}
        </span>
      </div>

      {/* ── 基本信息 ── */}
      <div className="settings-form-grid">
        <div><label className="form-label">系统名称</label><input className="form-input" value={systemName} onChange={(e) => onSystemNameChange(e.target.value)} placeholder="例：电商平台"/></div>
        <div><label className="form-label">模块名称</label><input className="form-input" value={moduleName} onChange={(e) => onModuleNameChange(e.target.value)} placeholder="例：订单管理"/></div>
        <div><label className="form-label">服务名称</label><input className="form-input" value={serviceName} onChange={(e) => onServiceNameChange(e.target.value)} placeholder="例：订单服务"/></div>
        <div><label className="form-label">测试地址 (Base URL)</label><input className="form-input form-input-mono" value={endpointRef} onChange={(e) => onEndpointRefChange(e.target.value)} placeholder="例如：http://order-service.internal:8080"/></div>
      </div>

      {/* ── API 认证 ── */}
      {hasEndpoint && (
        <div className="settings-auth-section">
          <div className="settings-section-title">🔑 API 认证方式</div>
          <div className="settings-auth-tabs">
            {(Object.keys(AUTH_TYPE_LABELS) as AuthType[]).map((t) => (
              <button
                key={t}
                className={`settings-auth-tab ${authType === t ? 'settings-auth-tab--active' : ''}`}
                onClick={() => onAuthTypeChange(t)}
                type="button"
              >
                {AUTH_TYPE_LABELS[t]}
              </button>
            ))}
          </div>

          {authType === 'password_login' && (
            <>
              {/* ── Admin (required, always visible) ── */}
              <div style={{ marginTop: 14 }}>
                <label className="form-label">Login API</label>
                <input
                  className="form-input form-input-mono"
                  value={loginApi}
                  onChange={(e) => onLoginApiChange(e.target.value)}
                  placeholder="/auth/login"
                />
              </div>
              <div style={{ marginTop: 14 }}>
                <span className="form-label" style={{ marginBottom: 6, display: 'block' }}>管理员账号</span>
                <div className="settings-role-row">
                  <div className="settings-role-col settings-role-col--role">
                    <span className="settings-role-badge">admin</span>
                  </div>
                  <div className="settings-role-col settings-role-col--user">
                    <input className="form-input form-input-sm" value={adminAccount.username}
                      onChange={(e) => updateAdmin('username', e.target.value)} placeholder="admin" />
                  </div>
                  <div className="settings-role-col settings-role-col--pass">
                    <input className="form-input form-input-sm" type="password" value={adminAccount.password}
                      onChange={(e) => updateAdmin('password', e.target.value)} placeholder="••••••••" />
                  </div>
                </div>
                <p className="settings-auth-hint">
                  admin 账号覆盖 API 契约、数据一致性、业务规则等 90% 检测，下图中的其他角色仅用于权限边界测试。
                </p>
              </div>

              {/* ── 权限测试账号（可选、折叠） ── */}
              <div className="settings-extra-roles">
                <div
                  className="settings-extra-roles-toggle"
                  onClick={() => setShowExtraRoles(!showExtraRoles)}
                >
                  <span>{showExtraRoles ? '▾' : '▸'} 🔒 权限测试账号（可选）</span>
                  {!showExtraRoles && extraAccounts.length > 0 && (
                    <span className="settings-extra-roles-count">{extraAccounts.length}个</span>
                  )}
                </div>

                {showExtraRoles && (
                  <div className="settings-extra-roles-body">
                    <p className="settings-auth-hint" style={{ marginBottom: 10 }}>
                      添加其他角色账号用于检测数据越权和权限边界问题（如 viewer 是否能访问 admin 数据）。
                    </p>
                    {extraAccounts.map((acc, idx) => {
                      const isCustom = !ROLE_OPTIONS.slice(0, -1).some(o => o.value === acc.role);
                      return (
                        <div key={idx} className="settings-role-row settings-role-row--extra">
                          <div className="settings-role-col settings-role-col--role">
                            {isCustom ? (
                              <input className="form-input form-input-sm" value={acc.role}
                                onChange={(e) => updateExtraRole(idx, 'role', e.target.value)} placeholder="角色名" />
                            ) : (
                              <select className="form-input form-input-sm" value={acc.role}
                                onChange={(e) => {
                                  if (e.target.value === '__custom__') updateExtraRole(idx, 'role', '');
                                  else updateExtraRole(idx, 'role', e.target.value);
                                }}>
                                {ROLE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                              </select>
                            )}
                          </div>
                          <div className="settings-role-col settings-role-col--user">
                            <input className="form-input form-input-sm" value={acc.username}
                              onChange={(e) => updateExtraRole(idx, 'username', e.target.value)}
                              placeholder={acc.role} />
                          </div>
                          <div className="settings-role-col settings-role-col--pass">
                            <input className="form-input form-input-sm" type="password" value={acc.password}
                              onChange={(e) => updateExtraRole(idx, 'password', e.target.value)} placeholder="••••••••" />
                          </div>
                          <button type="button" className="settings-role-remove-btn"
                            onClick={() => removeExtraRole(idx)} title="移除">✕</button>
                        </div>
                      );
                    })}
                    <button type="button" className="settings-role-add-btn" onClick={addExtraRole}>
                      + 添加角色
                    </button>
                  </div>
                )}
              </div>

              {/* ── 登录接口自动探测（用户无需关心） ── */}
              <p className="settings-auth-hint" style={{ marginTop: 14, marginBottom: 0 }}>
                系统启动时自动探测登录接口：依次尝试 <code>/auth/login</code>、<code>/api/auth/login</code>、<code>/login</code> 等常见路径，找到可用的即自动使用。
              </p>
            </>
          )}

          {authType === 'bearer_token' && (
            <div style={{ marginTop: 14 }}>
              <label className="form-label">Bearer Token</label>
              <textarea className="form-input form-input-mono" rows={3} value={bearerToken}
                onChange={(e) => onBearerTokenChange(e.target.value)}
                placeholder="eyJhbGciOiJIUzI1NiIs..."
                style={{ resize: 'vertical', maxWidth: '100%' }} />
            </div>
          )}

          {authType === 'api_key' && (
            <div style={{ marginTop: 14 }}>
              <label className="form-label">API Key</label>
              <input className="form-input form-input-mono" type="password" value={apiKey}
                onChange={(e) => onApiKeyChange(e.target.value)} placeholder="sk-..." style={{ maxWidth: 420 }} />
            </div>
          )}
        </div>
      )}

      <DbCredentialPanel
        dbOpen={dbOpen} credentialSummary={credentialSummary}
        dbType={dbType} dbHost={dbHost} dbPort={dbPort}
        dbUser={dbUser} dbPass={dbPass} dbName={dbName}
        dbTestLoad={dbTestLoad} dbTestOk={dbTestOk}
        dbTestMsg={dbTestMsg} dbTestHintText={dbTestHintText}
        dbOptions={dbOptions} getDbDefaultPort={getDbDefaultPort}
        onToggleOpen={onToggleDbPanel}
        onDbTypeChange={onDbTypeChange} onDbHostChange={onDbHostChange}
        onDbPortChange={onDbPortChange} onDbUserChange={onDbUserChange}
        onDbPassChange={onDbPassChange} onDbNameChange={onDbNameChange}
        onTest={onDbTest} onApplyCredentialRef={onApplyCredentialRef}
      />

      <div className="settings-form-actions">
        <label className="settings-enable-toggle"><input type="checkbox" checked={enabled} onChange={(e) => onEnabledChange(e.target.checked)}/> 启用</label>
        <button onClick={onSave} className="btn btn-primary settings-btn-compact">保存</button>
        <button onClick={onCancel} className="btn btn-secondary settings-btn-compact">取消</button>
      </div>
      {statusText && <p className="settings-inline-feedback">{statusText}</p>}
    </div>
  );
}
