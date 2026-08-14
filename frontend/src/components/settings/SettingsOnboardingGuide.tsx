import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { getKnowledgeAsset, getServiceCredentials, listConnectors, type ConnectorRecord } from '../../api/client';
import { listKnowledgeConnectors } from '../../api/knowledge-connectors';
import { useProjectNavigation } from '../../lib/project-navigation';
import { hasConfiguredAuthMaterial, hasConfiguredDbMaterial, type SavedServiceConfig } from '../../lib/settings-utils';
import { asRecord } from '../../lib/value-guards';

type SettingsOnboardingGuideProps = {
  project: string;
};

type SetupSnapshot = {
  connectors: ConnectorRecord[];
  services: SavedServiceConfig[];
  knowledgeConnectorCount: number;
  materialCount: number;
  onlineMaterialCount: number;
  uploadedMaterialCount: number;
};

type MaterialStatus = Pick<SetupSnapshot, 'materialCount' | 'onlineMaterialCount' | 'uploadedMaterialCount'>;

function extractServices(payload: unknown): SavedServiceConfig[] {
  const root = asRecord(payload);
  return Array.isArray(root.services)
    ? root.services.map((item) => asRecord(item) as SavedServiceConfig)
    : [];
}

function extractMaterialStatus(payload: unknown): MaterialStatus {
  const root = asRecord(payload);
  const asset = asRecord(root.knowledge_asset || root.data || root);
  const inventory = Array.isArray(asset.sources)
    ? asset.sources
    : Array.isArray(asset.source_inventory)
      ? asset.source_inventory
      : [];
  const sources = inventory
    .map(asRecord)
    .filter((source) => String(source.status || 'active').toLowerCase() !== 'deleted');
  const onlineMaterialCount = sources.filter((source) => (
    String(source.source_origin || '').toUpperCase() === 'ONLINE_CONNECTOR'
    || String(source.source_ref || '').startsWith('connector://')
  )).length;

  return {
    materialCount: sources.length,
    onlineMaterialCount,
    uploadedMaterialCount: Math.max(0, sources.length - onlineMaterialCount),
  };
}

const EMPTY_SNAPSHOT: SetupSnapshot = {
  connectors: [],
  services: [],
  knowledgeConnectorCount: 0,
  materialCount: 0,
  onlineMaterialCount: 0,
  uploadedMaterialCount: 0,
};

export function SettingsOnboardingGuide({ project }: SettingsOnboardingGuideProps) {
  const { navigateToProjectPath } = useProjectNavigation();
  const [snapshot, setSnapshot] = useState<SetupSnapshot>(EMPTY_SNAPSHOT);
  const [loading, setLoading] = useState(false);
  const [loadWarning, setLoadWarning] = useState('');
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    if (!project) {
      setSnapshot(EMPTY_SNAPSHOT);
      setLoadWarning('');
      return;
    }

    let cancelled = false;
    let firstLoad = true;

    const refreshSnapshot = async () => {
      if (firstLoad) {
        setLoading(true);
        setLoadWarning('');
      }

      const [connectorsResult, servicesResult, materialsResult, knowledgeConnectorsResult] = await Promise.allSettled([
        listConnectors(project),
        getServiceCredentials(project),
        getKnowledgeAsset(project),
        listKnowledgeConnectors(project),
      ]);
      if (cancelled) return;

      const connectors = connectorsResult.status === 'fulfilled' ? connectorsResult.value : [];
      const services = servicesResult.status === 'fulfilled' ? extractServices(servicesResult.value) : [];
      const materialStatus = materialsResult.status === 'fulfilled'
        ? extractMaterialStatus(materialsResult.value)
        : { materialCount: 0, onlineMaterialCount: 0, uploadedMaterialCount: 0 };
      const knowledgeConnectorCount = knowledgeConnectorsResult.status === 'fulfilled'
        ? knowledgeConnectorsResult.value.connectors.length
        : 0;
      const failedReads = [connectorsResult, servicesResult, materialsResult, knowledgeConnectorsResult]
        .filter((result) => result.status === 'rejected').length;

      setSnapshot({ connectors, services, knowledgeConnectorCount, ...materialStatus });
      setLoadWarning(failedReads > 0 ? '部分接入状态暂时无法读取，请重新核对后再判断是否已经完成接入。' : '');
      if (firstLoad) {
        firstLoad = false;
        setLoading(false);
      }
    };

    const handleRefresh = () => {
      void refreshSnapshot();
    };

    void refreshSnapshot();
    const timer = window.setInterval(refreshSnapshot, 15_000);
    window.addEventListener('qualibug:settings-onboarding-refresh', handleRefresh);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener('qualibug:settings-onboarding-refresh', handleRefresh);
    };
  }, [project, refreshNonce]);

  const enabledServices = useMemo(
    () => snapshot.connectors.filter((connector) => connector.enabled).length,
    [snapshot.connectors],
  );
  const authCount = useMemo(
    () => snapshot.services.filter((service) => hasConfiguredAuthMaterial(service)).length,
    [snapshot.services],
  );
  const dbCount = useMemo(
    () => snapshot.services.filter((service) => hasConfiguredDbMaterial(service)).length,
    [snapshot.services],
  );

  const requiredCompleted = [enabledServices > 0, authCount > 0, snapshot.materialCount > 0].filter(Boolean).length;
  const setupReady = requiredCompleted === 3 && !loadWarning;
  const materialsHref = project ? `/materials?project=${encodeURIComponent(project)}` : '/materials';

  const scrollToSystemAccess = () => {
    document.getElementById('settings-system-access')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const recheck = () => {
    setRefreshNonce((value) => value + 1);
  };

  if (!project) {
    return (
      <section className="section-card settings-span-2">
        <span className="panel-kicker">首次接入</span>
        <h2>先选择一个客户项目</h2>
        <p className="settings-card-sub">选择客户后，系统会按真实完成状态引导系统地址、测试账号、企业资料和可选数据库配置。</p>
      </section>
    );
  }

  const materialValue = snapshot.onlineMaterialCount > 0
    ? `${snapshot.onlineMaterialCount} 份在线资料已接入`
    : snapshot.knowledgeConnectorCount > 0
      ? `${snapshot.knowledgeConnectorCount} 个在线来源已连接`
      : snapshot.materialCount > 0
        ? `${snapshot.uploadedMaterialCount} 份文件补充已接入`
        : '待连接';
  const materialNote = snapshot.onlineMaterialCount > 0
    ? `在线资料作为主来源持续同步${snapshot.uploadedMaterialCount > 0 ? `，另有 ${snapshot.uploadedMaterialCount} 份文件补充` : ''}。`
    : snapshot.knowledgeConnectorCount > 0
      ? `在线资料源已经连接，正在等待首次读取形成可用资料${snapshot.uploadedMaterialCount > 0 ? `；当前另有 ${snapshot.uploadedMaterialCount} 份文件补充` : ''}。`
      : snapshot.materialCount > 0
        ? '已有文件补充，可继续运行前检查；建议连接企业在线资料以保持内容持续更新。'
        : '优先连接企业在线文档、知识库等在线资料源；文件上传只作为缺失资料的补充方式。';

  const steps = [
    {
      key: 'service',
      title: '1. 系统地址',
      value: enabledServices > 0 ? `${enabledServices} 个服务可用` : '待接入',
      note: enabledServices > 0 ? '已具备可执行的测试环境入口。' : '至少接入一个测试环境地址，真实执行才能开始。',
      tone: enabledServices > 0 ? 'success' : 'warning',
      action: <button type="button" className="btn btn-secondary settings-btn-mini" onClick={scrollToSystemAccess}>{enabledServices > 0 ? '管理地址' : '接入系统'}</button>,
    },
    {
      key: 'auth',
      title: '2. 测试账号',
      value: authCount > 0 ? `${authCount} 组可复用` : '待配置',
      note: authCount > 0 ? '账号、Token 或 API Key 已可用于真实登录。' : '缺少鉴权材料时，登录后业务链可能被阻断。',
      tone: authCount > 0 ? 'success' : 'warning',
      action: <button type="button" className="btn btn-secondary settings-btn-mini" onClick={scrollToSystemAccess}>{authCount > 0 ? '管理凭据' : '补充账号'}</button>,
    },
    {
      key: 'materials',
      title: '3. 企业资料',
      value: materialValue,
      note: materialNote,
      tone: snapshot.materialCount > 0 ? 'success' : 'warning',
      action: <Link className="btn btn-secondary settings-btn-mini" to={materialsHref}>{snapshot.onlineMaterialCount > 0 || snapshot.knowledgeConnectorCount > 0 ? '查看资料源' : '连接在线资料'}</Link>,
    },
    {
      key: 'database',
      title: '4. 数据库（可选）',
      value: dbCount > 0 ? `${dbCount} 组已配置` : '可跳过',
      note: dbCount > 0 ? '可补充数据库一致性与状态变化证据。' : '没有数据库权限也可以先完成接口/UI 侧验证。',
      tone: dbCount > 0 ? 'success' : 'neutral',
      action: <button type="button" className="btn btn-secondary settings-btn-mini" onClick={scrollToSystemAccess}>{dbCount > 0 ? '管理数据库' : '需要时配置'}</button>,
    },
  ];

  const mainAction = loadWarning
    ? { label: '重新核对接入状态', kind: 'refresh' as const }
    : enabledServices === 0
      ? { label: '先接入系统地址', kind: 'system' as const }
      : authCount === 0
        ? { label: '补充测试账号', kind: 'system' as const }
        : snapshot.materialCount === 0
          ? { label: snapshot.knowledgeConnectorCount > 0 ? '查看在线资料同步' : '连接企业在线资料', kind: 'materials' as const }
          : { label: '继续运行前检查', kind: 'campaigns' as const };

  const handleMainAction = () => {
    if (mainAction.kind === 'refresh') {
      recheck();
      return;
    }
    if (mainAction.kind === 'system') {
      scrollToSystemAccess();
      return;
    }
    if (mainAction.kind === 'materials') {
      navigateToProjectPath('/materials', project);
      return;
    }
    navigateToProjectPath('/campaigns', project);
  };

  return (
    <section className="section-card settings-span-2">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">首次接入向导</span>
          <h2>{setupReady ? '基础接入已完成' : `完成 ${requiredCompleted}/3 个必需步骤`}</h2>
          <p className="settings-card-sub">
            企业资料以在线资料源接入为主，文件上传仅作补充；只有真实可读资料形成后才计入首次资料完成状态，最终仍由运行前检查判定是否可执行。
          </p>
        </div>
        <span className={`summary-pill ${setupReady ? 'strong' : ''}`}>
          {loading ? '正在核对状态…' : setupReady ? '已具备运行前检查条件' : loadWarning ? '状态需要重新核对' : '继续完成接入'}
        </span>
      </div>

      <div className="customer-summary-grid settings-mt-10">
        {steps.map((step) => (
          <article key={step.key} className={`customer-summary-card tone-${step.tone}`}>
            <span>{step.title}</span>
            <strong>{step.value}</strong>
            <small>{step.note}</small>
            <div className="settings-mt-10">{step.action}</div>
          </article>
        ))}
      </div>

      <div className="settings-actions settings-mt-10">
        <button type="button" className="btn btn-primary" onClick={handleMainAction} disabled={loading}>
          {loading ? '正在核对接入状态…' : mainAction.label}
        </button>
        {!loading && !loadWarning && (
          <button type="button" className="btn btn-secondary" onClick={recheck}>重新核对状态</button>
        )}
      </div>

      <p className="settings-hint settings-mt-10">
        {setupReady
          ? snapshot.onlineMaterialCount > 0
            ? '下一步进入运行中心。在线资料会继续按连接器能力保持更新；运行前检查仍会使用后端真实门禁核对必要条件。'
            : '当前通过文件补充资料已满足首次资料条件，可以进入运行中心；后续建议连接在线资料源以减少人工维护。运行前检查仍是最终执行门禁。'
          : snapshot.knowledgeConnectorCount > 0 && snapshot.materialCount === 0
            ? '在线资料源已经连接，但首次同步尚未形成真实可读资料，因此当前不会提前标记资料步骤完成。'
            : '接入表单会自动保存本次浏览器会话中的非敏感草稿；账号密码、Token、API Key 和数据库认证信息不会写入草稿。'}
      </p>
      {loadWarning && <p className="settings-inline-feedback" role="alert">{loadWarning}</p>}
    </section>
  );
}
