import { useEffect, useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { usePageTitle } from '../lib/page-title';
import {
  getServiceCredentials,
  saveServiceCredentials,
  testDbConnection,
} from '../api/client';

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

type AuthType = 'password_login' | 'bearer_token' | 'api_key';
type ServiceStatus = 'verified' | 'configured_unverified' | 'no_token' | 'no_config';

interface ServiceConfig {
  name: string;
  base_url: string;
  enabled: boolean;
  login_api: string;
  auth_type: AuthType;
  admin_user: string;
  admin_pass: string;
  bearer_token: string;
  api_key: string;
  db_host: string;
  db_port: string;
  db_name: string;
  db_user: string;
  db_pass: string;
  db_test_status: 'idle' | 'testing' | 'ok' | 'fail';
  db_test_msg: string;
  status: ServiceStatus;
}

type ServiceCredentialRecord = Partial<ServiceConfig> & {
  auth?: Record<string, unknown>;
  db?: Record<string, unknown>;
};

type ServiceCredentialsResponse = {
  services?: ServiceCredentialRecord[];
};

type SaveServiceCredentialsResponse = {
  auth_check?: {
    all_ok?: boolean;
  };
};

const BLANK_SERVICE: ServiceConfig = {
  name: '',
  base_url: '',
  enabled: true,
  login_api: '/auth/login',
  auth_type: 'password_login',
  admin_user: '',
  admin_pass: '',
  bearer_token: '',
  api_key: '',
  db_host: '',
  db_port: '',
  db_name: '',
  db_user: '',
  db_pass: '',
  db_test_status: 'idle',
  db_test_msg: '',
  status: 'no_config',
};

function hasAuthMaterial(service: Partial<ServiceConfig>): boolean {
  return Boolean(service.bearer_token || service.api_key || service.admin_user);
}

function hasNestedAuthMaterial(service: { auth?: Record<string, unknown> }): boolean {
  const auth = service.auth || {};
  const admin = auth.admin && typeof auth.admin === 'object' ? auth.admin as Record<string, unknown> : {};
  return Boolean(
    auth.bearer_token_configured || auth.api_key_configured ||
    auth.bearer_token || auth.api_key ||
    admin.username || admin.password_configured || admin.password,
  );
}

function serviceStatusFromRecord(service: Partial<ServiceConfig> & { auth?: Record<string, unknown> }): ServiceStatus {
  if (service.status === 'verified') return 'verified';
  if (service.status === 'configured_unverified') return 'configured_unverified';
  if (hasAuthMaterial(service) || hasNestedAuthMaterial(service)) return 'configured_unverified';
  return service.base_url ? 'no_token' : 'no_config';
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function normalizeServiceConfig(service: ServiceCredentialRecord): ServiceConfig {
  const auth = service.auth && typeof service.auth === 'object' ? service.auth : {};
  const admin = auth.admin && typeof auth.admin === 'object' ? auth.admin as Record<string, unknown> : {};
  const db = service.db && typeof service.db === 'object' ? service.db : {};
  const authType = text(service.auth_type) || text(auth.type) || BLANK_SERVICE.auth_type;
  return {
    ...BLANK_SERVICE,
    ...service,
    login_api: text(service.login_api) || text(auth.login_api) || BLANK_SERVICE.login_api,
    auth_type: (['password_login', 'bearer_token', 'api_key'] as AuthType[]).includes(authType as AuthType) ? authType as AuthType : BLANK_SERVICE.auth_type,
    admin_user: text(service.admin_user) || text(admin.username),
    admin_pass: text(service.admin_pass) || (admin.password_configured ? '********' : ''),
    bearer_token: text(service.bearer_token) || (auth.bearer_token_configured ? '********' : ''),
    api_key: text(service.api_key) || (auth.api_key_configured ? '********' : ''),
    db_host: text(service.db_host) || text(db.host),
    db_port: text(service.db_port) || (db.port == null ? '' : String(db.port)),
    db_name: text(service.db_name) || text(db.name),
    db_user: text(service.db_user) || text(db.user),
    db_pass: text(service.db_pass) || (db.password_configured ? '********' : ''),
    status: serviceStatusFromRecord(service),
  };
}

/* ------------------------------------------------------------------ */
/* Component                                                           */
/* ------------------------------------------------------------------ */

export function ServiceCredentials() {
  usePageTitle('凭证管理');

  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [services, setServices] = useState<ServiceConfig[]>([]);
  const [editing, setEditing] = useState<number | null>(null);

  /* ---- load ---- */
  const load = useCallback(async () => {
    if (!project) { setLoading(false); return; }
    setLoading(true);
    try {
      const data = await getServiceCredentials(project) as ServiceCredentialsResponse;
      const svcs: ServiceConfig[] = (Array.isArray(data?.services) ? data.services : []).map(normalizeServiceConfig);
      setServices(svcs.length ? svcs : []);
    } catch {
      setServices([]);
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => { load(); }, [load]);

  /* ---- save single service ---- */
  const saveService = useCallback(async (index: number) => {
    const svc = services[index];
    if (!svc?.name.trim() || !svc?.base_url.trim()) return;
    setSaving(svc.name);
    try {
      const result = await saveServiceCredentials({
        project,
        service: {
          name: svc.name.trim(),
          base_url: svc.base_url.trim(),
          enabled: svc.enabled,
          login_api: svc.login_api.trim() || '/auth/login',
          auth_type: svc.auth_type,
          admin_user: svc.admin_user.trim(),
          admin_pass: svc.admin_pass,
          bearer_token: svc.bearer_token.trim(),
          api_key: svc.api_key.trim(),
          db_host: svc.db_host.trim(),
          db_port: svc.db_port,
          db_name: svc.db_name.trim(),
          db_user: svc.db_user.trim(),
          db_pass: svc.db_pass,
        },
      }) as SaveServiceCredentialsResponse;
      const verified = Boolean(result.auth_check?.all_ok);
      setServices(prev => prev.map((s, i) => i === index ? { ...s, status: verified ? 'verified' as const : 'configured_unverified' as const } : s));
    } catch {
      // keep editing open so user can fix
    } finally {
      setSaving(null);
    }
  }, [project, services]);

  /* ---- db test ---- */
  const testDb = useCallback(async (index: number) => {
    const svc = services[index];
    if (!svc?.db_host) return;
    setServices(prev => prev.map((s, i) => i === index ? { ...s, db_test_status: 'testing' as const, db_test_msg: '测试中...' } : s));
    try {
      const result = await testDbConnection(JSON.stringify({
        type: 'mysql', host: svc.db_host, port: Number(svc.db_port) || 3306,
        user: svc.db_user, password: svc.db_pass, database: svc.db_name,
      }));
      setServices(prev => prev.map((s, i) => i === index ? {
        ...s,
        db_test_status: result.ok ? 'ok' as const : 'fail' as const,
        db_test_msg: result.message || result.error || (result.ok ? '连接成功' : '连接失败'),
      } : s));
    } catch {
      setServices(prev => prev.map((s, i) => i === index ? { ...s, db_test_status: 'fail' as const, db_test_msg: '连接失败' } : s));
    }
  }, [services]);

  /* ---- add / remove ---- */
  const addService = () => {
    const svc = { ...BLANK_SERVICE, name: `service-${services.length + 1}`, login_api: '/auth/login' };
    setServices(prev => [...prev, svc]);
    setEditing(services.length);
  };

  const removeService = (index: number) => {
    setServices(prev => prev.filter((_, i) => i !== index));
    if (editing === index) setEditing(null);
  };

  const updateField = (index: number, field: keyof ServiceConfig, value: string | boolean) => {
    setServices(prev => prev.map((s, i) => i === index ? { ...s, [field]: value } : s));
  };

  /* ---- render ---- */
  if (loading) return <div className="svc-creds-loading">加载中...</div>;

  return (
    <div className="svc-creds-page">
      <div className="svc-creds-header">
        <div>
          <h1>🔐 服务凭证管理</h1>
          <p className="svc-creds-subtitle">
            每个微服务独立配置认证方式。支持密码登录、Bearer Token、API Key 三种模式。
          </p>
        </div>
        <button className="svc-creds-add-btn" onClick={addService}>+ 添加服务</button>
      </div>

      {services.length === 0 && (
        <div className="svc-creds-empty">
          <div className="svc-creds-empty-icon">📦</div>
          <h3>还没有配置任何服务</h3>
          <p>点击「添加服务」开始配置第一个微服务的凭证</p>
          <button className="svc-creds-add-btn" onClick={addService}>+ 添加服务</button>
        </div>
      )}

      {services.map((svc, index) => (
        <div key={index} className={`svc-card ${editing === index ? 'svc-card--open' : ''}`}>
          {/* ---- Card header ---- */}
          <div className="svc-card-header" onClick={() => setEditing(editing === index ? null : index)}>
            <div className="svc-card-header-left">
              <span className={`svc-card-dot svc-dot--${svc.status}`} />
              <div>
                <span className="svc-card-name">{svc.name || '未命名服务'}</span>
                {svc.base_url && <span className="svc-card-url">{svc.base_url}</span>}
              </div>
            </div>
            <div className="svc-card-header-right">
              {svc.status === 'verified' && <span className="svc-badge svc-badge--ok">已验证</span>}
              {svc.status === 'configured_unverified' && <span className="svc-badge svc-badge--warn">已配置待验证</span>}
              {svc.status === 'no_token' && <span className="svc-badge svc-badge--warn">待认证</span>}
              {svc.status === 'no_config' && <span className="svc-badge svc-badge--new">未配置</span>}
              <span className="svc-card-chevron">{editing === index ? '▲' : '▼'}</span>
            </div>
          </div>

          {/* ---- Card body (expandable) ---- */}
          {editing === index && (
            <div className="svc-card-body">
              {/* 基本信息 */}
              <div className="svc-section">
                <h4>📋 基本信息</h4>
                <div className="svc-grid-2">
                  <label>
                    服务名称
                    <input
                      placeholder="例: order-service"
                      value={svc.name}
                      onChange={e => updateField(index, 'name', e.target.value)}
                    />
                  </label>
                  <label>
                    服务地址 (Base URL)
                    <input
                      placeholder="例: http://order.internal:8080"
                      value={svc.base_url}
                      onChange={e => updateField(index, 'base_url', e.target.value)}
                    />
                  </label>
                </div>
                <label style={{ marginTop: 12 }}>
                  登录接口路径
                  <input
                    placeholder="/auth/login"
                    value={svc.login_api}
                    onChange={e => updateField(index, 'login_api', e.target.value)}
                    style={{ maxWidth: 360 }}
                  />
                </label>
              </div>

              {/* 认证方式 */}
              <div className="svc-section">
                <h4>🔑 认证方式</h4>
                <div className="svc-auth-tabs">
                  {(['password_login', 'bearer_token', 'api_key'] as AuthType[]).map(t => (
                    <button
                      key={t}
                      className={`svc-auth-tab ${svc.auth_type === t ? 'svc-auth-tab--active' : ''}`}
                      onClick={() => updateField(index, 'auth_type', t)}
                    >
                      {t === 'password_login' && '🔐 密码登录'}
                      {t === 'bearer_token' && '🎫 Bearer Token'}
                      {t === 'api_key' && '🔑 API Key'}
                    </button>
                  ))}
                </div>

                {svc.auth_type === 'password_login' && (
                  <div className="svc-grid-2" style={{ marginTop: 12 }}>
                    <label>
                      管理员用户名
                      <input
                        placeholder="admin"
                        value={svc.admin_user}
                        onChange={e => updateField(index, 'admin_user', e.target.value)}
                      />
                    </label>
                    <label>
                      管理员密码
                      <input
                        type="password"
                        placeholder="••••••••"
                        value={svc.admin_pass}
                        onChange={e => updateField(index, 'admin_pass', e.target.value)}
                      />
                    </label>
                    <p className="svc-hint" style={{ gridColumn: '1 / -1' }}>
                      系统启动时自动用此凭证调用 {svc.login_api || '/auth/login'} 获取 Token，无需手动维护 Token 过期。
                    </p>
                  </div>
                )}

                {svc.auth_type === 'bearer_token' && (
                  <div style={{ marginTop: 12 }}>
                    <label>
                      Bearer Token
                      <textarea
                        rows={3}
                        placeholder="eyJhbGciOiJIUzI1NiIs..."
                        value={svc.bearer_token}
                        onChange={e => updateField(index, 'bearer_token', e.target.value)}
                        style={{ fontFamily: 'monospace', fontSize: 13 }}
                      />
                    </label>
                    <p className="svc-hint">
                      也可通过环境变量 QUALIBUG_SVC_{svc.name.toUpperCase().replace(/-/g, '_')}_BEARER_TOKEN 注入
                    </p>
                  </div>
                )}

                {svc.auth_type === 'api_key' && (
                  <div style={{ marginTop: 12 }}>
                    <label>
                      API Key
                      <input
                        type="password"
                        placeholder="sk-..."
                        value={svc.api_key}
                        onChange={e => updateField(index, 'api_key', e.target.value)}
                        style={{ maxWidth: 480 }}
                      />
                    </label>
                    <p className="svc-hint">API Key 将通过 X-API-Key 头传递到服务端</p>
                  </div>
                )}
              </div>

              {/* 数据库（可折叠） */}
              <details className="svc-section svc-db-section">
                <summary><h4 style={{ display: 'inline' }}>🗄️ 数据库连接（可选 — 用于一致性验证）</h4></summary>
                <div className="svc-grid-3" style={{ marginTop: 12 }}>
                  <label>主机 <input placeholder="localhost" value={svc.db_host} onChange={e => updateField(index, 'db_host', e.target.value)} /></label>
                  <label>端口 <input placeholder="3306" value={svc.db_port} onChange={e => updateField(index, 'db_port', e.target.value)} /></label>
                  <label>数据库名 <input placeholder="order_db" value={svc.db_name} onChange={e => updateField(index, 'db_name', e.target.value)} /></label>
                  <label>用户名 <input placeholder="svc_user" value={svc.db_user} onChange={e => updateField(index, 'db_user', e.target.value)} /></label>
                  <label>密码 <input type="password" placeholder="••••" value={svc.db_pass} onChange={e => updateField(index, 'db_pass', e.target.value)} /></label>
                  <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8 }}>
                    <button
                      className="svc-btn-test"
                      disabled={svc.db_test_status === 'testing'}
                      onClick={() => testDb(index)}
                    >
                      {svc.db_test_status === 'testing' ? '测试中...' : '测试连接'}
                    </button>
                    {svc.db_test_status !== 'idle' && (
                      <span className={`svc-db-msg svc-db-msg--${svc.db_test_status}`}>
                        {svc.db_test_msg}
                      </span>
                    )}
                  </div>
                </div>
              </details>

              {/* 操作按钮 */}
              <div className="svc-card-actions">
                <button className="svc-btn-delete" onClick={() => removeService(index)}>
                  删除此服务
                </button>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="svc-btn-cancel" onClick={() => { setEditing(null); load(); }}>
                    取消
                  </button>
                  <button
                    className="svc-btn-save"
                    disabled={saving === svc.name || !svc.name.trim() || !svc.base_url.trim()}
                    onClick={() => saveService(index)}
                  >
                    {saving === svc.name ? '保存中...' : '保存'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      ))}

      {services.length > 0 && (
        <div className="svc-creds-footer">
          <button className="svc-creds-add-btn" onClick={addService}>+ 添加服务</button>
        </div>
      )}
    </div>
  );
}
