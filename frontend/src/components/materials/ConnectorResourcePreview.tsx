/**
 * 连接器发现的资源预览卡片。拆分自 pages/Materials.tsx。
 */
import type { ConnectorResourceInventory } from '../../api/knowledge-connectors';
import { formatTime, permissionScopeLabel } from '../../lib/materials-presentation';

export function ConnectorResourcePreview({ preview }: { preview?: ConnectorResourceInventory }) {
  if (!preview || preview.status === 'NOT_AVAILABLE') return null;
  return (
    <section className="connector-resource-preview" aria-label="发现资源预览">
      <div className="connector-resource-preview-heading">
        <span>发现资源预览</span>
        <strong>{preview.discovered_count} 项 · 已接入 {preview.covered_count} 项</strong>
      </div>
      {preview.resources.length === 0 ? (
        <p>尚未形成可展示的资源摘要，完成首次同步后会自动更新。</p>
      ) : (
        <div className="connector-resource-preview-list">
          {preview.resources.slice(0, 5).map((resource) => (
            <article key={resource.resource_index}>
              <strong>{resource.display_title || '未命名资源'}</strong>
              <span>{resource.remote_object_type || resource.resource_kind || resource.state}</span>
              <small>
                {resource.updated_at_utc
                  ? `最近观测 · ${formatTime(resource.updated_at_utc, '暂无记录')}`
                  : '尚未记录更新时间'}
                {resource.source_updated_at ? ` · 来源更新标记 · ${resource.source_updated_at}` : ''}
                {resource.permission_scope ? ` · ${permissionScopeLabel(resource.permission_scope)}` : ''}
              </small>
            </article>
          ))}
        </div>
      )}
      {preview.preview_truncated && <small>资源较多，当前仅展示前 100 项摘要。</small>}
    </section>
  );
}
