import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { getHealth, getServiceCredentials, listConnectors, login, registerConnector, saveSettings, saveServiceCredentials, testDbConnection, type ConnectorRecord } from '../api/client';
import { useLiveStatus, useWorkspaceDirectory } from '../api/data';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { SettingsCustomerSection } from '../components/settings/SettingsCustomerSection';
import { SettingsMetadataSection } from '../components/settings/SettingsMetadataSection';
import { SettingsTopologySection } from '../components/settings/SettingsTopologySection';
import { SettingsLlmSection } from '../components/settings/SettingsLlmSection';
import { SettingsAdvancedToolsSection } from '../components/settings/SettingsAdvancedToolsSection';
import { SettingsServiceForm } from '../components/settings/SettingsServiceForm';
import { buildSettingsTopologyViewModel } from '../lib/settings-topology';
import {
  DB_OPTIONS, getDbDefaultPort, getErrorMessage, buildTenantId,
  asRecord, asString, normalizeRoleAccounts,
  extractRoleAccounts, extractAuthType, extractLoginApi, extractBearerToken, extractApiKey,
  extractDbConfig, findMatchingServiceConfig,
  type TenantCreateResponse, type SavedServiceConfig,
} from '../lib/settings-utils';

function createSecureTemporaryPassword(length = 24): string {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%';
  const values = new Uint32Array(length);
  window.crypto.getRandomValues(values);
  return Array.from(values, (value) => alphabet[value % alphabet.length]).join('');
}

export function Settings() {
  usePageTitle('系统与环境');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { switchProject } = useProjectNavigation();
  const { scanActive, hasMaterializedMetrics } = useLiveStatus(project, 15000);
  const { workspaces, workspaceOptions, loadError: workspaceLoadError, refresh: refreshWorkspaces } = useWorkspaceDirectory();
  const [wsStatus, setWsStatus] = useState('');
  const [importId, setImportId] = useState('');
  const [llmUrl, setLlmUrl] = useState(''); const [llmModel, setLlmModel] = useState(''); const [llmKey, setLlmKey] = useState(''); const [llmStatus, setLlmStatus] = useState(''); const [llmError, setLlmError] = useState('');
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [connectors, setConnectors] = useState<ConnectorRecord[]>([]);
  const [serviceConfigs, setServiceConfigs] = useState<SavedServiceConfig[]>([]);
  const [cStatus, setCStatus] = useState('');
  const [formOpen, setFormOpen] = useState(false); const [editId, setEditId] = useState('');
  const [cSys, setCSys] = useState(''); const [cMod, setCMod] = useState(''); const [cName, setCName] = useState('');
  const [cEp, setCEp] = useState(''); const [cCred, setCCred] = useState(''); const [cEn, setCEn] = useState(true);
  const [editingServiceConfigName, setEditingServiceConfigName] = useState('');
  const [hlId, setHlId] = useState('');
  // API Auth
  const [cAuthType, setCAuthType] = useState<'password_login'|'bearer_token'|'api_key'>('password_login');
  const [cLoginApi, setCLoginApi] = useState('/auth/login');
  const [cRoleAccounts, setCRoleAccounts] = useState<Array<{role:string;username:string;password:string}>>([
    { role: 'admin', username: '', password: '' },
  ]);
  const [cBearerToken, setCBearerToken] = useState(''); const [cApiKey, setCApiKey] = useState('');
  // DB form
  const [dbOpen, setDbOpen] = useState(false);
  const [dbType, setDbType] = useState('postgresql');
  const [dbHost, setDbHost] = useState(''); const [dbPort, setDbPort] = useState('5432');
  const [dbUser, setDbUser] = useState(''); const [dbPass, setDbPass] = useState(''); const [dbName, setDbName] = useState('');
  const [dbTestMsg, setDbTestMsg] = useState(''); const [dbTestOk, setDbTestOk] = useState(false); const [dbTestLoad, setDbTestLoad] = useState(false);
  const [expSys, setExpSys] = useState<Set<string>>(new Set());

  useEffect(() => { let c=false; getHealth().then(d=>{ if(!c) setHealth(d&&typeof d==='object'?d as Record<string,unknown>:null); }).catch(()=>{ if(!c) setHealth(null); }); return ()=>{c=true}; }, []);
  useEffect(() => {
    setWsStatus('');
    if (project || workspaceOptions.length !== 1) return;
    switchProject(workspaceOptions[0].id);
  }, [project, switchProject, workspaceOptions]);
  useEffect(() => { if(!cStatus.startsWith('✓')) return; const t=window.setTimeout(()=>setCStatus(''),3000); return ()=>window.clearTimeout(t); }, [cStatus]);
  useEffect(() => { if(!hlId) return; const t=window.setTimeout(()=>setHlId(''),4000); return ()=>window.clearTimeout(t); }, [hlId]);
  useEffect(() => { let c=false; setCStatus(''); if(!project){ setConnectors([]); return ()=>{c=true}; }
    listConnectors(project).then(r=>{ if(!c) setConnectors(r); }).catch(()=>{ if(!c) setConnectors([]); }); return ()=>{c=true}; }, [project]);
  useEffect(() => {
    let cancelled = false;
    if (!project) {
      setServiceConfigs([]);
      return () => { cancelled = true; };
    }
    getServiceCredentials(project)
      .then((payload) => {
        if (cancelled) return;
        const data = asRecord(payload);
        const services = Array.isArray(data.services) ? data.services : [];
        setServiceConfigs(services.map((item) => asRecord(item) as SavedServiceConfig));
      })
      .catch(() => {
        if (!cancelled) setServiceConfigs([]);
      });
    return () => { cancelled = true; };
  }, [project]);

  const reC = async () => { if(!project){ setConnectors([]); return []; } try{ setCStatus('加载中...'); const r=await listConnectors(project); setConnectors(r); setCStatus(''); return r; } catch(e:unknown){ setCStatus(`✗ ${e instanceof Error?e.message:'加载失败'}`); return []; } };
  const refreshServiceConfigs = async () => {
    if (!project) {
      setServiceConfigs([]);
      return [];
    }
    try {
      const payload = await getServiceCredentials(project);
      const data = asRecord(payload);
      const services = Array.isArray(data.services) ? data.services : [];
      const next = services.map((item) => asRecord(item) as SavedServiceConfig);
      setServiceConfigs(next);
      return next;
    } catch {
      setServiceConfigs([]);
      return [];
    }
  };
  const toSys = (s:string)=>{ setExpSys(p=>{ const n=new Set(p); if(n.has(s)) n.delete(s); else n.add(s); return n; }); };
  const buildDsn = ()=>{ if(!dbHost.trim()) return ''; const port=dbPort.trim()||String(DB_OPTIONS.find(d=>d.v===dbType)?.p||''); const a=dbUser.trim()?`${dbUser.trim()}:${dbPass}@`:''; const db=dbName.trim()?`/${dbName.trim()}`:''; return `${dbType}://${a}${dbHost.trim()}:${port}${db}`; };
  const refSlug = (value:string)=>value.trim().toUpperCase().replace(/[^A-Z0-9]+/g,'_').replace(/^_+|_+$/g,'').slice(0,64)||'DATABASE';
  const defaultCredentialRef = ()=>`secret_ref:${refSlug([project,cSys,cMod,cName,dbType].filter(Boolean).join('_'))}`;
  const credentialLabel = (value?:string)=>{ const ref=String(value||'').trim(); if(!ref)return ''; if(ref.includes('://')||ref.includes('@')) return '<凭证已隐藏>'; return ref; };
  const resetDbDraft = (nextType = 'postgresql') => {
    setDbOpen(false);
    setDbType(nextType);
    setDbHost('');
    setDbPort(getDbDefaultPort(nextType));
    setDbUser('');
    setDbPass('');
    setDbName('');
    setDbTestMsg('');
    setDbTestOk(false);
    setDbTestLoad(false);
  };
  const openCreateConnectorForm = (systemName = '', moduleName = '') => {
    setEditId('');
    setEditingServiceConfigName('');
    setCSys(systemName);
    setCMod(moduleName);
    setCName('');
    setCEp('');
    setCCred('');
    setCEn(true);
    setCAuthType('password_login');
    setCLoginApi('/auth/login');
    setCRoleAccounts([{ role: 'admin', username: '', password: '' }]);
    setCBearerToken(''); setCApiKey('');
    resetDbDraft();
    setFormOpen(true);
  };
  const openEditConnectorForm = (connector: ConnectorRecord) => {
    const matchedConfig = findMatchingServiceConfig(connector, serviceConfigs);
    const dbConfig = extractDbConfig(matchedConfig);
    setEditId(connector.connector_id);
    setCSys(connector.system_name || '');
    setCMod(connector.module_name || '');
    setCName(connector.display_name || asString(matchedConfig?.name) || '');
    setCEp(connector.endpoint_ref || '');
    setCCred(credentialLabel(connector.credential_ref));
    setCEn(connector.enabled);
    setEditingServiceConfigName(asString(matchedConfig?.name));
    setCAuthType(extractAuthType(matchedConfig));
    setCLoginApi(extractLoginApi(matchedConfig));
    setCRoleAccounts(extractRoleAccounts(matchedConfig));
    setCBearerToken(extractBearerToken(matchedConfig));
    setCApiKey(extractApiKey(matchedConfig));
    setDbOpen(false);
    setDbType(dbConfig.type);
    setDbHost(dbConfig.host);
    setDbPort(dbConfig.port || getDbDefaultPort(dbConfig.type));
    setDbUser(dbConfig.user);
    setDbPass(dbConfig.password);
    setDbName(dbConfig.name);
    setDbTestMsg('');
    setDbTestOk(false);
    setDbTestLoad(false);
    setFormOpen(true);
  };
  const closeConnectorForm = () => {
    setFormOpen(false);
    setEditingServiceConfigName('');
    setDbTestMsg('');
    setDbTestOk(false);
    setDbTestLoad(false);
  };
  const handleWorkspaceRefresh = async () => {
    setWsStatus('刷新中...');
    await refreshWorkspaces(true);
    setWsStatus('');
  };
  const handleWorkspaceChange = (value: string) => {
    if (value) switchProject(value);
  };
  const handleCreateWorkspace = async () => {
    const name = importId.trim();
    if (!name) {
      setWsStatus('✗ 请输入公司名称');
      return;
    }

    setWsStatus('创建中...');
    try {
      const tenantId = buildTenantId(name);
      const temporaryPassword = createSecureTemporaryPassword();
      const response = await fetch('/api/tenants/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenant_id: tenantId,
          name,
          username: tenantId,
          password: temporaryPassword,
        }),
      });
      const payload = (await response.json()) as TenantCreateResponse;

      if (payload.ok) {
        const loginOk = await login(tenantId, temporaryPassword);
        const items = await refreshWorkspaces(true);
        setImportId('');
        const nextProjectId = items.find((item) => item.project_id === tenantId)?.project_id || tenantId;
        switchProject(nextProjectId);
        setWsStatus(loginOk ? '✓ 创建成功，已切换到新客户' : '✓ 创建成功，请重新登录后查看该客户数据');
        return;
      }

      setWsStatus(`✗ ${payload.error || '创建失败'}`);
    } catch (error: unknown) {
      setWsStatus(`✗ ${getErrorMessage(error, '创建失败')}`);
    }
  };
  const handleConnectorStatusChange = async (connector: ConnectorRecord, enabled: boolean) => {
    setCStatus('处理中...');
    try {
      await registerConnector({
        project_id: project,
        connector_id: connector.connector_id,
        display_name: connector.display_name,
        kind: connector.kind || 'http_api',
        enabled,
        system_name: connector.system_name || connector.display_name || undefined,
        module_name: connector.module_name || undefined,
        endpoint_ref: connector.endpoint_ref || undefined,
        credential_ref: connector.credential_ref || undefined,
      });
      await reC();
      setCStatus(`✓ 已${enabled ? '启用' : '停用'}`);
    } catch (error: unknown) {
      setCStatus(`✗ ${getErrorMessage(error, '失败')}`);
    }
  };
  const handleDbTypeChange = (value: string) => {
    setDbType(value);
    setDbPort(getDbDefaultPort(value));
  };
  const toggleDbPanel = () => {
    setDbOpen((open) => !open);
  };
  const handleDbTest = async () => {
    const dsn = buildDsn();
    if (!dsn) {
      setDbTestMsg('请填写主机地址');
      return;
    }

    setDbTestLoad(true);
    setDbTestMsg('');
    setDbTestOk(false);
    try {
      const result = await testDbConnection(dsn);
      setDbTestOk(result.ok);
      setDbTestMsg(result.ok ? `✓ ${result.message || '连接成功'}` : `✗ ${result.error || '连接失败'}`);
    } catch {
      setDbTestMsg('✗ 请求失败');
    } finally {
      setDbTestLoad(false);
    }
  };
  const handleApplyCredentialRef = () => {
    setCCred(defaultCredentialRef());
    setDbOpen(false);
    setDbTestMsg('');
  };
  const handleSaveConnector = async () => {
    if (!project) {
      setCStatus('请选择客户');
      return;
    }

    if (!cSys.trim()) {
      setCStatus('请填写系统名称');
      return;
    }

    const displayName = cName.trim() || [cSys.trim(), cMod.trim()].filter(Boolean).join(' / ') || '未命名服务';
    const serviceConfigName = cName.trim() || cSys.trim().toLowerCase().replace(/\s+/g, '-') || editingServiceConfigName || 'service';
    const roleAccounts = normalizeRoleAccounts(cRoleAccounts);
    const persistedRoleAccounts = roleAccounts.filter((account) => account.role && (account.username.trim() || account.password));
    const hasCredentialMaterial = persistedRoleAccounts.length > 0
      || Boolean(cBearerToken.trim())
      || Boolean(cApiKey.trim())
      || Boolean(dbHost.trim() || dbName.trim() || dbUser.trim() || dbPass);
    const shouldSaveCredentials = Boolean(editId || editingServiceConfigName || hasCredentialMaterial || cEp.trim());
    setCStatus('保存中...');
    try {
      // 1. Save connector (existing)
      await registerConnector({
        project_id: project,
        connector_id: editId || undefined,
        display_name: displayName,
        kind: 'http_api',
        enabled: cEn,
        system_name: cSys.trim(),
        module_name: cMod.trim() || undefined,
        endpoint_ref: cEp.trim() || undefined,
      });

      // 2. Save API credentials to multi_service_config.json (new)
      let authStatusNote = '';
      if (shouldSaveCredentials) {
        const authResponse = await saveServiceCredentials({
          project,
          previous_name: editingServiceConfigName || undefined,
          service: {
            name: serviceConfigName,
            base_url: cEp.trim(),
            enabled: cEn,
            login_api: cLoginApi === '__HIDE__' ? '/auth/login' : cLoginApi.trim() || '/auth/login',
            auth_type: cAuthType,
            role_accounts: persistedRoleAccounts.map((account) => ({
              role: account.role,
              username: account.username.trim(),
              password: account.password,
            })),
            bearer_token: cBearerToken.trim(),
            api_key: cApiKey.trim(),
            db_host: dbHost.trim(),
            db_port: dbPort,
            db_name: dbName.trim(),
            db_user: dbUser.trim(),
            db_pass: dbPass,
          },
        });
        const authCheck = asRecord(asRecord(authResponse).auth_check);
        const roles = asRecord(authCheck.roles);
        const authVerified = authCheck.all_ok === true;
        const failedRoles = Object.entries(roles)
          .filter(([, ok]) => ok === false)
          .map(([role]) => role);
        if (authVerified) {
          authStatusNote = '，测试账号已校验通过';
        } else if (failedRoles.length > 0) {
          authStatusNote = `，但以下测试账号登录失败：${failedRoles.join('、')}`;
        } else {
          authStatusNote = '，凭据已保存但尚未通过在线验证';
        }
        setEditingServiceConfigName(serviceConfigName);
      }

      await reC();
      await refreshServiceConfigs();
      closeConnectorForm();
      setEditId('');
      setCStatus(`✓ 已保存${authStatusNote}`);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (error: unknown) {
      setCStatus(`✗ ${getErrorMessage(error, '保存失败')}`);
    }
  };
  const handleSaveLlmSettings = async () => {
    const payload: Record<string, unknown> = {};
    if (llmUrl.trim()) payload.llm_base_url = llmUrl.trim();
    if (llmModel.trim()) payload.llm_model = llmModel.trim();
    if (llmKey.trim()) payload.llm_api_key = llmKey.trim();
    if (!Object.keys(payload).length) {
      setLlmStatus('未修改');
      return;
    }

    setLlmStatus('verifying');
    try {
      const result = await saveSettings(payload);
      const output = result && typeof result === 'object' ? (result as Record<string, unknown>) : {};
      const available = output.llm_available;
      const error = String(output.llm_error || '');
      setLlmError(error);
      setLlmStatus(error ? 'error' : available ? 'ok' : 'error');
    } catch (error: unknown) {
      setLlmStatus('error');
      setLlmError(getErrorMessage(error, '请求失败'));
    }
  };

  const topology = useMemo(
    () => buildSettingsTopologyViewModel(connectors, expSys, hlId, credentialLabel),
    [connectors, expSys, hlId],
  );
  const llmL = useMemo(()=>{ const s=health&&typeof health==='object'?(health as Record<string,unknown>).llm_status:null; return String((s&&typeof s==='object'?(s as Record<string,unknown>).label||(s as Record<string,unknown>).status:'')||'未验证'); },[health]);
  const pv=useMemo(()=>String((health&&typeof health==='object'?(health as Record<string,unknown>).version:'')||'').trim()||'未知',[health]);
  const ss=!project?'未选择客户':scanActive?'扫描运行中':hasMaterializedMetrics?'运行中':'已选择客户 · 暂无数据';
  const as=!project?'未选择客户':hasMaterializedMetrics?'完整':'暂无记录';
  const statusToneClass = hasMaterializedMetrics||scanActive ? 'is-positive' : 'is-neutral';
  const workspaceLabel = workspaceOptions.find((item) => item.id === project)?.label || '未选择客户';
  const llmHealthy = llmStatus === 'ok' || llmL.includes('online') || llmL.includes('OK');
  const llmStateText = llmStatus==='verifying'
    ? '检测中...'
    : llmStatus==='error'
      ? `连接失败${llmError?`: ${llmError.slice(0,60)}`:''}`
      : llmHealthy
        ? '已连接'
        : llmStatus==='未修改'
          ? '未修改'
          : '待验证';
  const llmStateTone = llmStatus==='verifying' ? 'warning' : llmStatus==='error' ? 'danger' : llmHealthy ? 'success' : 'neutral';
  const dbTestHintText = !dbHost.trim()
    ? '请填写主机地址和端口'
    : (dbUser.trim() && dbPass ? '将验证 TCP 连通性和数据库认证' : '仅验证 TCP 端口连通性');

  const serviceForm = (
    <SettingsServiceForm
      open={formOpen}
      title={editId ? '编辑服务' : '新增服务'}
      systemName={cSys}
      moduleName={cMod}
      serviceName={cName}
      endpointRef={cEp}
      enabled={cEn}
      statusText={cStatus}
      credentialSummary={credentialLabel(cCred)}
      authType={cAuthType}
      loginApi={cLoginApi}
      roleAccounts={cRoleAccounts}
      bearerToken={cBearerToken}
      apiKey={cApiKey}
      dbOpen={dbOpen}
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
      dbOptions={DB_OPTIONS}
      getDbDefaultPort={getDbDefaultPort}
      onSystemNameChange={setCSys}
      onModuleNameChange={setCMod}
      onServiceNameChange={setCName}
      onEndpointRefChange={setCEp}
      onEnabledChange={setCEn}
      onAuthTypeChange={setCAuthType}
      onLoginApiChange={setCLoginApi}
      onRoleAccountsChange={setCRoleAccounts}
      onBearerTokenChange={setCBearerToken}
      onApiKeyChange={setCApiKey}
      onToggleDbPanel={toggleDbPanel}
      onDbTypeChange={handleDbTypeChange}
      onDbHostChange={setDbHost}
      onDbPortChange={setDbPort}
      onDbUserChange={setDbUser}
      onDbPassChange={setDbPass}
      onDbNameChange={setDbName}
      onDbTest={handleDbTest}
      onApplyCredentialRef={handleApplyCredentialRef}
      onSave={handleSaveConnector}
      onCancel={closeConnectorForm}
    />
  );

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">项目接入</span>
          <h1>系统与环境</h1>
          <p>选择客户、接入被测系统，其余结构由后台自动理解。</p>
        </div>
      </div>

      <div className="settings-layout">
        <SettingsCustomerSection
          workspaceLabel={workspaceLabel}
          workspacesCount={workspaces.length}
          project={project}
          workspaceOptions={workspaceOptions}
          importId={importId}
          wsStatus={wsStatus || workspaceLoadError}
          workspaceLoadFailed={Boolean(workspaceLoadError) && workspaceOptions.length === 0}
          onWorkspaceChange={handleWorkspaceChange}
          onRefresh={handleWorkspaceRefresh}
          onImportIdChange={setImportId}
          onCreateWorkspace={handleCreateWorkspace}
        />
        <SettingsMetadataSection project={project} />
        <SettingsTopologySection
          project={project}
          topology={topology}
          onToggleSystem={toSys}
          onOpenCreateConnector={(systemName) => openCreateConnectorForm(systemName || '')}
          onOpenEditConnector={openEditConnectorForm}
          onToggleConnectorStatus={handleConnectorStatusChange}
        >
          {serviceForm}
        </SettingsTopologySection>
        <SettingsLlmSection
          llmStateTone={llmStateTone}
          llmHealthy={llmHealthy}
          llmStateText={llmStateText}
          llmError={llmError}
          llmUrl={llmUrl}
          llmModel={llmModel}
          llmKey={llmKey}
          onLlmUrlChange={setLlmUrl}
          onLlmModelChange={setLlmModel}
          onLlmKeyChange={setLlmKey}
          onSaveAndVerify={handleSaveLlmSettings}
        />
        <SettingsAdvancedToolsSection
          project={project}
          productVersion={pv}
          serviceStatus={ss}
          auditStatus={as}
          statusToneClass={statusToneClass}
        />
      </div>
    </div>
  );
}

export default Settings;
