import { useCallback, useEffect, useState } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { getKnowledgeAsset } from '../../api/client';
import { useProjectNavigation } from '../../lib/project-navigation';

type MaterialSnapshot = {
  total: number;
  active: number;
  processing: number;
  failed: number;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
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

  return {
    total: sources.length,
    active: sources.filter((source) => String(source.status || 'active').toLowerCase() === 'active').length,
    processing: sources.filter((source) => String(source.status || '').toLowerCase() === 'processing').length,
    failed: sources.filter((source) => ['failed', 'degraded'].includes(String(source.status || '').toLowerCase())).length,
  };
}

export function MaterialsOnboardingHandoff() {
  const location = useLocation();
  const [params] = useSearchParams();
  const { navigateToProjectPath } = useProjectNavigation();
  const project = params.get('project')?.trim() || '';
  const [snapshot, setSnapshot] = useState<MaterialSnapshot>({ total: 0, active: 0, processing: 0, failed: 0 });
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
  const tone = readError || snapshot.failed > 0 ? 'warning' : cleanReady ? 'success' : 'warning';
  const title = readError
    ? '企业资料已接入，但最新状态暂时无法重新核对'
    : snapshot.failed > 0
      ? `${snapshot.failed} 份资料存在异常，建议处理后再形成完整理解`
      : snapshot.processing > 0
        ? `${snapshot.processing} 份资料仍在处理，可以同时完成系统接入`
        : '企业资料已接入，下一步配置系统与环境';
  const detail = readError
    ? '当前保留已读取到的资料状态，不把读取失败解释为资料缺失。可以重新核对，或先继续配置系统地址和测试账号。'
    : cleanReady
      ? `${snapshot.active} 份资料当前可用。完成系统地址和测试账号后，进入运行中心执行真实运行前检查。`
      : `当前共 ${snapshot.total} 份资料，其中 ${snapshot.active} 份已生效、${snapshot.processing} 份处理中、${snapshot.failed} 份异常。`;

  return (
    <section className={`card mb-4 status-card status-${tone}`} aria-label="首次接入下一步">
      <span className="panel-kicker">首次接入 · 下一步</span>
      <h2>{title}</h2>
      <p className="muted">{detail}</p>
      <div className="settings-actions">
        {cleanReady ? (
          <>
            <button type="button" className="btn btn-primary" onClick={() => navigateToProjectPath('/settings', project)}>下一步：系统与环境</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/campaigns', project)}>已配置过？运行前检查</button>
          </>
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
