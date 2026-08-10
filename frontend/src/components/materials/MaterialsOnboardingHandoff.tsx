import { useCallback, useEffect, useState } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset } from '../../api/client';
import { listKnowledgeConnectors } from '../../api/knowledge-connectors';
import { useProjectNavigation } from '../../lib/project-navigation';

const BUSINESS_CONTEXT_TYPES = new Set([
  'prd',
  'openapi',
  'database_schema',
  'db_design',
  'collaboration_document',
  'historical_bug',
]);

type MaterialSnapshot = {
  connectorCount: number;
  total: number;
  active: number;
  onlineActive: number;
  uploadedActive: number;
  businessContextActive: number;
  processing: number;
  failed: number;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function isOnlineSource(source: Record<string, unknown>): boolean {
  return String(source.source_origin || '').toUpperCase() === 'ONLINE_CONNECTOR'
    || String(source.source_ref || '').startsWith('connector://');
}

function readMaterialSnapshot(payload: unknown): Omit<MaterialSnapshot, 'connectorCount'> {
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
  const activeSources = sources.filter((source) => String(source.status || 'active').toLowerCase() === 'active');
  const onlineActive = activeSources.filter(isOnlineSource).length;
  const businessContextActive = activeSources.filter((source) => (
    BUSINESS_CONTEXT_TYPES.has(String(source.source_type || '').trim().toLowerCase())
  )).length;

  return {
    total: sources.length,
    active: activeSources.length,
    onlineActive,
    uploadedActive: Math.max(0, activeSources.length - onlineActive),
    businessContextActive,
    processing: sources.filter((source) => String(source.status || '').toLowerCase() === 'processing').length,
    failed: sources.filter((source) => ['failed', 'degraded'].includes(String(source.status || '').toLowerCase())).length,
  };
}

export function MaterialsOnboardingHandoff() {
  const location = useLocation();
  const [params] = useSearchParams();
  const { navigateToProjectPath } = useProjectNavigation();
  const project = params.get('project')?.trim() || '';
  const [snapshot, setSnapshot] = useState<MaterialSnapshot>({
    connectorCount: 0,
    total: 0,
    active: 0,
    onlineActive: 0,
    uploadedActive: 0,
    businessContextActive: 0,
    processing: 0,
    failed: 0,
  });
  const [loading, setLoading] = useState(false);
  const [materialReadError, setMaterialReadError] = useState('');
  const [connectorReadError, setConnectorReadError] = useState('');

  const refresh = useCallback(async () => {
    if (!project || location.pathname !== '/materials') return;
    setLoading(true);
    const [materialsResult, connectorsResult] = await Promise.allSettled([
      getKnowledgeAsset(project),
      listKnowledgeConnectors(project),
    ]);

    if (materialsResult.status === 'fulfilled') {
      const materialSnapshot = readMaterialSnapshot(materialsResult.value);
      setSnapshot((current) => ({ ...current, ...materialSnapshot }));
      setMaterialReadError('');
    } else {
      setMaterialReadError(materialsResult.reason instanceof Error ? materialsResult.reason.message : '资料状态读取失败');
    }

    if (connectorsResult.status === 'fulfilled') {
      setSnapshot((current) => ({ ...current, connectorCount: connectorsResult.value.connectors.length }));
      setConnectorReadError('');
    } else {
      setConnectorReadError(connectorsResult.reason instanceof Error ? connectorsResult.reason.message : '在线资料源状态读取失败');
    }
    setLoading(false);
  }, [location.pathname, project]);

  useEffect(() => {
    if (!project || location.pathname !== '/materials') return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => window.clearInterval(timer);
  }, [location.pathname, project, refresh]);

  if (location.pathname !== '/materials' || !project) return null;

  const cleanReady = snapshot.active > 0 && snapshot.processing === 0 && snapshot.failed === 0 && !materialReadError;
  const onlyUploadedReady = cleanReady && snapshot.onlineActive === 0 && snapshot.uploadedActive > 0;
  const tone = materialReadError || snapshot.failed > 0 ? 'warning' : cleanReady ? 'success' : 'warning';
  const title = materialReadError
    ? '企业资料状态暂时无法完整核对'
    : snapshot.failed > 0
      ? `${snapshot.failed} 份资料存在异常，建议处理后再形成完整理解输入`
      : snapshot.processing > 0
        ? `${snapshot.processing} 份资料仍在处理，可以同时完成系统接入`
        : onlyUploadedReady
          ? '补充文件已可用，建议继续连接企业在线资料源'
          : snapshot.onlineActive > 0
            ? '企业在线资料已同步并进入知识主链'
            : snapshot.connectorCount > 0
              ? '企业在线资料源已连接，等待首次同步形成可读资料'
              : '优先连接企业在线资料源';
  const detail = materialReadError
    ? '当前不会把读取失败解释为资料缺失或业务理解完成。可以重新核对，或先继续配置系统地址和测试账号。'
    : onlyUploadedReady
      ? `当前 ${snapshot.uploadedActive} 份文件补充资料已经可用，因此不会阻塞首次运行；但在线资料才是默认主来源，连接后可以持续同步企业最新文档，减少重复上传。`
      : cleanReady
        ? `${snapshot.onlineActive} 份在线资料当前可用${snapshot.uploadedActive > 0 ? `，另有 ${snapshot.uploadedActive} 份文件补充` : ''}。这里仅表示资料已经进入 QualiBug 输入主链，不代表业务理解已经正确或完整。`
        : snapshot.connectorCount > 0
          ? `已有 ${snapshot.connectorCount} 个在线资料源完成连接，但当前尚未形成可读取的在线资料；不会提前把“连接成功”解释成“资料已同步”。`
          : '先连接企业在线文档、知识库或其他后端已声明支持的资料源；文件上传只用于补充在线来源没有覆盖的资料。';

  const sourceStage = connectorReadError
    ? { value: '状态待核对', note: '在线资料源状态读取失败，不把失败解释为未连接。', tone: 'warning' }
    : snapshot.connectorCount > 0
      ? { value: `${snapshot.connectorCount} 个已连接`, note: '已建立在线资料来源；连接成功不等于资料已经完成同步。', tone: 'success' }
      : snapshot.onlineActive > 0
        ? { value: '已存在在线来源', note: '已观察到在线资料，但连接器清单暂未返回对应实例。', tone: 'warning' }
        : { value: '待连接', note: '默认先连接企业在线资料源。', tone: 'warning' };

  const syncStage = materialReadError
    ? { value: '状态待核对', note: '资料读取失败，不能判断同步结果。', tone: 'warning' }
    : snapshot.onlineActive > 0
      ? { value: `${snapshot.onlineActive} 份在线资料可用`, note: snapshot.processing > 0 ? `另有 ${snapshot.processing} 份仍在处理。` : '在线资料已经形成真实可读来源。', tone: 'success' }
      : snapshot.connectorCount > 0
        ? { value: '等待首次读取', note: '在线来源已经连接，但尚未形成可读在线资料。', tone: 'warning' }
        : snapshot.uploadedActive > 0
          ? { value: '当前仅文件补充', note: `${snapshot.uploadedActive} 份补充文件可用；建议继续连接在线来源。`, tone: 'neutral' }
          : { value: '等待资料', note: '尚无真实可读资料。', tone: 'warning' };

  const understandingStage = materialReadError
    ? { value: '无法确认', note: '资料状态不可读时，不推导业务理解输入就绪。', tone: 'warning' }
    : snapshot.businessContextActive > 0
      ? { value: `${snapshot.businessContextActive} 份核心输入已就绪`, note: '这些资料已进入业务理解输入主链；这里只代表输入可用，不代表理解正确率或完整性。', tone: 'success' }
      : snapshot.active > 0
        ? { value: '输入主链已建立', note: '已有资料可读，但 PRD / API / DB / 协作文档 / 历史缺陷等核心输入仍待补齐。', tone: 'warning' }
        : { value: '等待可读资料', note: '必须先形成真实 active source，前端才会显示业务理解输入已建立。', tone: 'warning' };

  const scrollToOnlineMaterials = () => {
    document.querySelector('.materials-primary-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <section className={`card mb-4 status-card status-${tone}`} aria-label="企业资料接入与业务理解输入就绪度">
      <span className="panel-kicker">企业资料 · 就绪链路</span>
      <h2>{title}</h2>
      <p className="muted">{detail}</p>

      <div className="customer-summary-grid settings-mt-10" aria-label="企业资料三层就绪状态">
        <article className={`customer-summary-card tone-${sourceStage.tone}`}>
          <span>1. 在线来源已连接</span>
          <strong>{sourceStage.value}</strong>
          <small>{sourceStage.note}</small>
        </article>
        <article className={`customer-summary-card tone-${syncStage.tone}`}>
          <span>2. 资料已同步</span>
          <strong>{syncStage.value}</strong>
          <small>{syncStage.note}</small>
        </article>
        <article className={`customer-summary-card tone-${understandingStage.tone}`}>
          <span>3. 业务理解输入</span>
          <strong>{understandingStage.value}</strong>
          <small>{understandingStage.note}</small>
        </article>
      </div>

      <div className="settings-actions settings-mt-10">
        {cleanReady ? (
          onlyUploadedReady ? (
            <>
              <button type="button" className="btn btn-primary" onClick={scrollToOnlineMaterials}>连接在线资料（推荐）</button>
              <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/settings', project)}>暂用补充资料，继续系统与环境</button>
              <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/campaigns', project)}>已配置过？运行前检查</button>
            </>
          ) : (
            <>
              <button type="button" className="btn btn-primary" onClick={() => navigateToProjectPath('/settings', project)}>下一步：系统与环境</button>
              <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/campaigns', project)}>已配置过？运行前检查</button>
            </>
          )
        ) : (
          <>
            {snapshot.connectorCount === 0 && !connectorReadError && (
              <button type="button" className="btn btn-primary" onClick={scrollToOnlineMaterials}>连接在线资料</button>
            )}
            <button type="button" className={snapshot.connectorCount === 0 && !connectorReadError ? 'btn btn-secondary' : 'btn btn-primary'} onClick={() => void refresh()} disabled={loading}>{loading ? '正在核对…' : '重新核对资料状态'}</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/settings', project)}>同时配置系统与环境</button>
          </>
        )}
      </div>

      {connectorReadError && <p className="settings-inline-feedback settings-mt-10" role="alert">在线资料源状态暂时无法核对：{connectorReadError}</p>}
    </section>
  );
}
