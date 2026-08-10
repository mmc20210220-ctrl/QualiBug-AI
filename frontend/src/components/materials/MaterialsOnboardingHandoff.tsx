import { useCallback, useEffect, useState } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset } from '../../api/client';
import { listKnowledgeConnectors, type KnowledgeConnectorRecord } from '../../api/knowledge-connectors';
import { materialSourceTypeLabel, normalizeMaterialSourceType } from '../../lib/material-type-presentation';
import { useProjectNavigation } from '../../lib/project-navigation';

const AUTHORIZATION_HEALTH = new Set([
  'REAUTHORIZATION_REQUIRED',
  'PERMISSION_INSUFFICIENT',
  'AUTHORIZATION_EXPIRING',
]);

const SYNC_FAILURE_HEALTH = new Set([
  'DEGRADED',
  'CALIBRATION_REQUIRED',
]);

type MaterialSnapshot = {
  connectorCount: number;
  authorizationAttentionCount: number;
  inactiveConnectorCount: number;
  syncFailureConnectorCount: number;
  syncingConnectorCount: number;
  downstreamDegradedCount: number;
  partialCoverageConnectorCount: number;
  total: number;
  active: number;
  onlineActive: number;
  uploadedActive: number;
  observedTypeCount: number;
  activeTypeCounts: Record<string, number>;
  processing: number;
  failed: number;
};

type MaterialNextAction = 'refresh' | 'connect' | 'review-connectors' | 'review-materials' | 'settings';

type MaterialBlocker = {
  headline: string;
  detail: string;
  action: MaterialNextAction;
  actionLabel: string;
  tone: 'success' | 'warning' | 'danger';
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

function readMaterialSnapshot(payload: unknown): Pick<MaterialSnapshot,
  | 'total'
  | 'active'
  | 'onlineActive'
  | 'uploadedActive'
  | 'observedTypeCount'
  | 'activeTypeCounts'
  | 'processing'
  | 'failed'> {
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
  const activeTypeCounts: Record<string, number> = {};

  activeSources.forEach((source) => {
    const type = normalizeMaterialSourceType(source.source_type);
    activeTypeCounts[type] = (activeTypeCounts[type] || 0) + 1;
  });

  return {
    total: sources.length,
    active: activeSources.length,
    onlineActive,
    uploadedActive: Math.max(0, activeSources.length - onlineActive),
    observedTypeCount: Object.keys(activeTypeCounts).length,
    activeTypeCounts,
    processing: sources.filter((source) => String(source.status || '').toLowerCase() === 'processing').length,
    failed: sources.filter((source) => ['failed', 'degraded'].includes(String(source.status || '').toLowerCase())).length,
  };
}

function readConnectorSnapshot(connectors: KnowledgeConnectorRecord[]): Pick<MaterialSnapshot,
  'connectorCount'
  | 'authorizationAttentionCount'
  | 'inactiveConnectorCount'
  | 'syncFailureConnectorCount'
  | 'syncingConnectorCount'
  | 'downstreamDegradedCount'
  | 'partialCoverageConnectorCount'> {
  const authorizationAttentionCount = connectors.filter((connector) => {
    const healthStatus = String(connector.health?.status || '').toUpperCase();
    const oauthStatus = String(connector.oauth?.status || '').toUpperCase();
    return AUTHORIZATION_HEALTH.has(healthStatus)
      || AUTHORIZATION_HEALTH.has(oauthStatus)
      || connector.health?.reauthorization_required === true
      || connector.connection_profile?.reauthorization_required === true;
  }).length;

  return {
    connectorCount: connectors.length,
    authorizationAttentionCount,
    inactiveConnectorCount: connectors.filter((connector) => (
      ['PAUSED', 'DISABLED'].includes(String(connector.status || '').toUpperCase())
      || ['PAUSED', 'DISABLED'].includes(String(connector.health?.status || '').toUpperCase())
    )).length,
    syncFailureConnectorCount: connectors.filter((connector) => (
      SYNC_FAILURE_HEALTH.has(String(connector.health?.status || '').toUpperCase())
    )).length,
    syncingConnectorCount: connectors.filter((connector) => (
      Boolean(connector.active_sync_epoch_id)
      || ['RUNNING', 'RETRYING'].includes(String(connector.auto_sync?.state || '').toUpperCase())
      || ['SYNCING', 'RETRYING'].includes(String(connector.health?.status || '').toUpperCase())
    )).length,
    downstreamDegradedCount: connectors.filter((connector) => (
      String(connector.health?.status || '').toUpperCase() === 'DOWNSTREAM_DEGRADED'
    )).length,
    partialCoverageConnectorCount: connectors.filter((connector) => (
      String(connector.health?.status || '').toUpperCase() === 'PARTIAL_COVERAGE'
      || String(connector.coverage?.status || '').toUpperCase() === 'PARTIAL_UNSUPPORTED'
      || (connector.coverage?.unsupported_count || 0) > 0
    )).length,
  };
}

function deriveCurrentBlocker(
  snapshot: MaterialSnapshot,
  materialReadError: string,
  connectorReadError: string,
): MaterialBlocker {
  if (materialReadError || connectorReadError) {
    return {
      headline: '企业资料状态需要重新核对',
      detail: '当前至少一个真实状态接口不可用；前端不会把读取失败解释成“未连接”“没有资料”或“业务理解已就绪”。',
      action: 'refresh',
      actionLabel: '重新核对资料状态',
      tone: 'warning',
    };
  }

  if (snapshot.authorizationAttentionCount > 0) {
    return {
      headline: `${snapshot.authorizationAttentionCount} 个在线资料源需要处理授权或权限`,
      detail: '这是当前最高优先级阻塞。请先恢复连接器授权或最小读取权限，再继续依赖后续同步结果。',
      action: 'review-connectors',
      actionLabel: '处理资料源授权',
      tone: 'danger',
    };
  }

  if (snapshot.inactiveConnectorCount > 0) {
    return {
      headline: `${snapshot.inactiveConnectorCount} 个在线资料源已暂停或关闭`,
      detail: '暂停或关闭的来源不会持续保持企业资料最新；请先确认是否需要恢复自动更新。',
      action: 'review-connectors',
      actionLabel: '查看资料源状态',
      tone: 'warning',
    };
  }

  if (snapshot.syncFailureConnectorCount > 0 || snapshot.failed > 0) {
    return {
      headline: '企业资料同步或处理存在失败项',
      detail: `当前 ${snapshot.syncFailureConnectorCount} 个在线资料源同步异常，${snapshot.failed} 份资料处于 failed/degraded。失败项不会被包装成可用输入。`,
      action: snapshot.syncFailureConnectorCount > 0 ? 'review-connectors' : 'review-materials',
      actionLabel: snapshot.syncFailureConnectorCount > 0 ? '查看同步异常' : '查看异常资料',
      tone: 'danger',
    };
  }

  if (snapshot.downstreamDegradedCount > 0) {
    return {
      headline: `${snapshot.downstreamDegradedCount} 个资料源已读取，但业务理解刷新尚未完成`,
      detail: '后端连接器已明确返回 DOWNSTREAM_DEGRADED。前端只说明下游刷新未完成，不会把“资料已读取”提前解释成业务理解已经更新。',
      action: 'review-connectors',
      actionLabel: '查看刷新状态',
      tone: 'warning',
    };
  }

  if (snapshot.syncingConnectorCount > 0 || snapshot.processing > 0) {
    return {
      headline: '企业资料正在同步或处理',
      detail: `当前 ${snapshot.syncingConnectorCount} 个在线资料源正在同步/重试，${snapshot.processing} 份资料仍在处理。完成前不会提前形成最终就绪结论。`,
      action: 'refresh',
      actionLabel: '重新核对最新状态',
      tone: 'warning',
    };
  }

  if (snapshot.partialCoverageConnectorCount > 0) {
    return {
      headline: `${snapshot.partialCoverageConnectorCount} 个在线资料源存在未支持资源`,
      detail: '已读取部分仍然可用，但连接器明确报告部分资源未覆盖；请先查看覆盖差距，避免把“部分同步”理解成“全部资料已接入”。',
      action: 'review-connectors',
      actionLabel: '查看未覆盖资料',
      tone: 'warning',
    };
  }

  if (snapshot.connectorCount === 0 && snapshot.active === 0) {
    return {
      headline: '尚未连接企业在线资料源',
      detail: '在线文档和知识库是默认主来源；请先建立真实在线连接，文件上传只用于补充在线来源没有覆盖的资料。',
      action: 'connect',
      actionLabel: '连接在线资料',
      tone: 'warning',
    };
  }

  if (snapshot.connectorCount > 0 && snapshot.active === 0) {
    return {
      headline: '在线资料源已连接，等待首次同步形成可读资料',
      detail: '连接器实例已经存在，但尚未 materialize 真实 source；Connection Ready 仍不等于 Material Ready。',
      action: 'refresh',
      actionLabel: '重新核对首次同步',
      tone: 'warning',
    };
  }

  if (snapshot.onlineActive === 0 && snapshot.uploadedActive > 0) {
    return {
      headline: '当前主要依赖文件补充资料',
      detail: '文件补充已经可用，不会阻塞首次运行；但建议连接企业在线资料源，以持续获取最新资料并减少重复上传。',
      action: 'connect',
      actionLabel: '连接在线资料（推荐）',
      tone: 'warning',
    };
  }

  return {
    headline: '企业资料输入主链已建立',
    detail: `${snapshot.active} 份真实资料已形成 active source，当前观察到 ${snapshot.observedTypeCount} 类资料类型。任何后端真实识别并成功接入的资料都可以成为输入；这里不设固定资料类型白名单，也不代表业务理解正确率或完整性。`,
    action: 'settings',
    actionLabel: '下一步：系统与环境',
    tone: 'success',
  };
}

export function MaterialsOnboardingHandoff() {
  const location = useLocation();
  const [params] = useSearchParams();
  const { navigateToProjectPath } = useProjectNavigation();
  const project = params.get('project')?.trim() || '';
  const [snapshot, setSnapshot] = useState<MaterialSnapshot>({
    connectorCount: 0,
    authorizationAttentionCount: 0,
    inactiveConnectorCount: 0,
    syncFailureConnectorCount: 0,
    syncingConnectorCount: 0,
    downstreamDegradedCount: 0,
    partialCoverageConnectorCount: 0,
    total: 0,
    active: 0,
    onlineActive: 0,
    uploadedActive: 0,
    observedTypeCount: 0,
    activeTypeCounts: {},
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
      const connectorSnapshot = readConnectorSnapshot(connectorsResult.value.connectors);
      setSnapshot((current) => ({ ...current, ...connectorSnapshot }));
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
      : snapshot.connectorCount > 0 && snapshot.active === 0
        ? { value: '等待首次读取', note: '在线来源已经连接，但尚未形成可读在线资料。', tone: 'warning' }
        : snapshot.uploadedActive > 0
          ? { value: '当前仅文件补充', note: `${snapshot.uploadedActive} 份补充文件可用；建议继续连接在线来源。`, tone: 'neutral' }
          : snapshot.active > 0
            ? { value: `${snapshot.active} 份资料可用`, note: '已形成真实 active source。', tone: 'success' }
            : { value: '等待资料', note: '尚无真实可读资料。', tone: 'warning' };

  const understandingStage = materialReadError
    ? { value: '无法确认', note: '资料状态不可读时，不推导业务理解输入就绪。', tone: 'warning' }
    : snapshot.active > 0
      ? { value: `${snapshot.active} 份资料已进入输入主链`, note: `已观察到 ${snapshot.observedTypeCount} 类真实资料类型；不设固定类型白名单，也不代表理解正确率或完整性。`, tone: 'success' }
      : { value: '等待可读资料', note: '必须先形成真实 active source，前端才会显示业务理解输入已建立。', tone: 'warning' };

  const observedTypes = Object.entries(snapshot.activeTypeCounts)
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .map(([type, count]) => ({ type, label: materialSourceTypeLabel(type), count }));

  const currentBlocker = deriveCurrentBlocker(snapshot, materialReadError, connectorReadError);

  const scrollToOnlineMaterials = () => {
    document.querySelector('.materials-primary-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const scrollToMaterialInventory = () => {
    document.querySelector('.materials-inventory-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const handleNextAction = () => {
    if (currentBlocker.action === 'refresh') {
      void refresh();
      return;
    }
    if (currentBlocker.action === 'connect' || currentBlocker.action === 'review-connectors') {
      scrollToOnlineMaterials();
      return;
    }
    if (currentBlocker.action === 'review-materials') {
      scrollToMaterialInventory();
      return;
    }
    navigateToProjectPath('/settings', project);
  };

  return (
    <section className={`card mb-4 status-card status-${currentBlocker.tone}`} aria-label="企业资料接入与业务理解输入就绪度">
      <span className="panel-kicker">企业资料 · 当前判断</span>
      <h2>{currentBlocker.headline}</h2>
      <p className="muted">{currentBlocker.detail}</p>

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

      <section className="status-card status-neutral settings-mt-10" aria-label="已接入资料类型分布">
        <span className="panel-kicker">资料类型分布</span>
        <strong>
          {materialReadError
            ? '当前无法确认资料类型分布'
            : snapshot.active > 0
              ? `已观察到 ${snapshot.observedTypeCount} 类 active 资料`
              : '等待形成可读资料'}
        </strong>
        <p className="muted">
          类型完全来自后端真实 source_type，前端不设固定资料白名单。UI / UX 设计、测试资料、架构文档以及未来新增类型都会自动进入这里；友好名称只是展示别名，未知类型会原样展示。
        </p>
        <div className="customer-summary-grid settings-mt-10">
          {materialReadError ? (
            <article className="customer-summary-card tone-warning">
              <span>资料类型</span>
              <strong>无法确认</strong>
              <small>资料状态不可读时，不把空结果解释成没有某类资料。</small>
            </article>
          ) : observedTypes.length > 0 ? observedTypes.map((item) => (
            <article key={item.type} className="customer-summary-card tone-success">
              <span>{item.label}</span>
              <strong>✓ {item.count} 份</strong>
              <small>source_type · {item.type}</small>
            </article>
          )) : (
            <article className="customer-summary-card tone-neutral">
              <span>资料类型</span>
              <strong>尚无 active 资料</strong>
              <small>同步或上传形成真实 active source 后自动展示类型分布。</small>
            </article>
          )}
        </div>
      </section>

      <div className={`status-card status-${currentBlocker.tone} settings-mt-10`} aria-label="企业资料当前最重要动作">
        <span className="panel-kicker">当前最重要动作</span>
        <strong>{currentBlocker.actionLabel}</strong>
        <p className="muted">只显示当前最高优先级动作；其他资料状态仍保留在下方连接器与统一资料清单中查看。</p>
        <div className="settings-actions settings-mt-10">
          <button type="button" className="btn btn-primary" onClick={handleNextAction} disabled={loading && currentBlocker.action === 'refresh'}>
            {loading && currentBlocker.action === 'refresh' ? '正在核对…' : currentBlocker.actionLabel}
          </button>
        </div>
      </div>

      {connectorReadError && <p className="settings-inline-feedback settings-mt-10" role="alert">在线资料源状态暂时无法核对：{connectorReadError}</p>}
      {materialReadError && <p className="settings-inline-feedback settings-mt-10" role="alert">企业资料状态暂时无法核对：{materialReadError}</p>}
    </section>
  );
}
