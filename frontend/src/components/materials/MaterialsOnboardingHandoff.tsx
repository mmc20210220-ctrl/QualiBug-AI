import { useCallback, useEffect, useState } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset } from '../../api/client';
import { useProjectNavigation } from '../../lib/project-navigation';

type MaterialSnapshot = {
  total: number;
  active: number;
  onlineActive: number;
  uploadedActive: number;
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

function readSnapshot(payload: unknown): MaterialSnapshot {
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

  return {
    total: sources.length,
    active: activeSources.length,
    onlineActive,
    uploadedActive: Math.max(0, activeSources.length - onlineActive),
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
    total: 0,
    active: 0,
    onlineActive: 0,
    uploadedActive: 0,
    processing: 0,
    failed: 0,
  });
  const [loading, setLoading] = useState(false);
  const [readError, setReadError] = useState('');

  const refresh = useCallback(async () => {
    if (!project || location.pathname !== '/materials') return;
    setLoading(true);
    try {
      const payload = await getKnowledgeAsset(project);
      setSnapshot(readSnapshot(payload));
      setReadError('');
    } catch (error: unknown) {
      setReadError(error instanceof Error ? error.message : '资料状态读取失败');
    } finally {
      setLoading(false);
    }
  }, [location.pathname, project]);

  useEffect(() => {
    if (!project || location.pathname !== '/materials') return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => window.clearInterval(timer);
  }, [location.pathname, project, refresh]);

  if (location.pathname !== '/materials' || !project || snapshot.total === 0) return null;

  const cleanReady = snapshot.active > 0 && snapshot.processing === 0 && snapshot.failed === 0 && !readError;
  const onlyUploadedReady = cleanReady && snapshot.onlineActive === 0 && snapshot.uploadedActive > 0;
  const tone = readError || snapshot.failed > 0 ? 'warning' : cleanReady ? 'success' : 'warning';
  const title = readError
    ? '企业资料已接入，但最新状态暂时无法重新核对'
    : snapshot.failed > 0
      ? `${snapshot.failed} 份资料存在异常，建议处理后再形成完整理解`
      : snapshot.processing > 0
        ? `${snapshot.processing} 份资料仍在处理，可以同时完成系统接入`
        : onlyUploadedReady
          ? '补充文件已可用，建议继续连接企业在线资料源'
          : '企业在线资料已接入，下一步配置系统与环境';
  const detail = readError
    ? '当前保留已读取到的资料状态，不把读取失败解释为资料缺失。可以重新核对，或先继续配置系统地址和测试账号。'
    : onlyUploadedReady
      ? `当前 ${snapshot.uploadedActive} 份文件补充资料已经可用，因此不会阻塞首次运行；但在线资料才是默认主来源，连接后可以持续同步企业最新文档，减少重复上传。`
      : cleanReady
        ? `${snapshot.onlineActive} 份在线资料当前可用${snapshot.uploadedActive > 0 ? `，另有 ${snapshot.uploadedActive} 份文件补充` : ''}。完成系统地址和测试账号后，进入运行中心执行真实运行前检查。`
        : `当前共 ${snapshot.total} 份资料，其中 ${snapshot.active} 份已生效、${snapshot.processing} 份处理中、${snapshot.failed} 份异常。`;

  const scrollToOnlineMaterials = () => {
    document.querySelector('.materials-primary-card')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <section className={`card mb-4 status-card status-${tone}`} aria-label="首次接入下一步">
      <span className="panel-kicker">首次接入 · 企业资料</span>
      <h2>{title}</h2>
      <p className="muted">{detail}</p>
      <div className="settings-actions">
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
            <button type="button" className="btn btn-primary" onClick={() => void refresh()} disabled={loading}>{loading ? '正在核对…' : '重新核对资料状态'}</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/settings', project)}>同时配置系统与环境</button>
          </>
        )}
      </div>
    </section>
  );
}
