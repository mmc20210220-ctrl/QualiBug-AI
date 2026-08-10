import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { getKnowledgeAsset, getServiceCredentials, listConnectors, type ConnectorRecord } from '../../api/client';
import { hasConfiguredAuthMaterial, hasConfiguredDbMaterial, type SavedServiceConfig } from '../../lib/settings-utils';

type SettingsOnboardingGuideProps = {
  project: string;
};

type SetupSnapshot = {
  connectors: ConnectorRecord[];
  services: SavedServiceConfig[];
  materialCount: number;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function extractServices(payload: unknown): SavedServiceConfig[] {
  const root = asRecord(payload);
  return Array.isArray(root.services)
    ? root.services.map((item) => asRecord(item) as SavedServiceConfig)
    : [];
}

function extractMaterialCount(payload: unknown): number {
  const root = asRecord(payload);
  const asset = asRecord(root.knowledge_asset || root.data || root);
  return Array.isArray(asset.sources) ? asset.sources.length : 0;
}

export function SettingsOnboardingGuide({ project }: SettingsOnboardingGuideProps) {
  const [snapshot, setSnapshot] = useState<SetupSnapshot>({ connectors: [], services: [], materialCount: 0 });
  const [loading, setLoading] = useState(false);
  const [loadWarning, setLoadWarning] = useState('');

  useEffect(() => {
    if (!project) {
      setSnapshot({ connectors: [], services: [], materialCount: 0 });
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

      const [connectorsResult, servicesResult, materialsResult] = await Promise.allSettled([
        listConnectors(project),
        getServiceCredentials(project),
        getKnowledgeAsset(project),
      ]);
      if (cancelled) return;

      const connectors = connectorsResult.status === 'fulfilled' ? connectorsResult.value : [];
      const services = servicesResult.status === 'fulfilled' ? extractServices(servicesResult.value) : [];
      const materialCount = materialsResult.status === 'fulfilled' ? extractMaterialCount(materialsResult.value) : 0;
      const failedReads = [connectorsResult, servicesResult, materialsResult].filter((result) => result.status === 'rejected').length;

      setSnapshot({ connectors, services, materialCount });
      setLoadWarning(failedReads > 0 ? '部分接入状态暂时无法读取，请以对应配置区的真实结果为准。' : '');
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
  }, [project]);

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
  const materialsHref = project ? `/materials?project=${encodeURIComponent(project)}` : '/materials';

  const scrollToSystemAccess = () => {
    document.getElementById('settings-system-access')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
      value: snapshot.materialCount > 0 ? `${snapshot.materialCount} 份已接入` : '待导入',
      note: snapshot.materialCount > 0 ? '已有资料可用于业务理解与测试规划。' : '建议至少提供 PRD、接口规范、数据库结构或历史缺陷中的一种。',
      tone: snapshot.materialCount > 0 ? 'success' : 'warning',
      action: <Link className="btn btn-secondary settings-btn-mini" to={materialsHref}>{snapshot.materialCount > 0 ? '管理资料' : '导入资料'}</Link>,
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

  return (
    <section className="section-card settings-span-2">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">首次接入向导</span>
          <h2>{requiredCompleted === 3 ? '基础接入已完成' : `完成 ${requiredCompleted}/3 个必需步骤`}</h2>
          <p className="settings-card-sub">
            按真实完成状态推进，不要求客户维护后台已经能够自动理解的结构；数据库属于增强证据能力，不阻塞首次体验。
          </p>
        </div>
        <span className={`summary-pill ${requiredCompleted === 3 ? 'strong' : ''}`}>
          {loading ? '正在核对状态…' : requiredCompleted === 3 ? '可以继续运行前检查' : '继续完成接入'}
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

      <p className="settings-hint settings-mt-10">
        接入表单会自动保存本次浏览器会话中的非敏感草稿；账号密码、Token、API Key 和数据库认证信息不会写入草稿。
      </p>
      {loadWarning && <p className="settings-inline-feedback">{loadWarning}</p>}
    </section>
  );
}
