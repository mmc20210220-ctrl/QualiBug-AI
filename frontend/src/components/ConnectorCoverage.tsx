import type {
  KnowledgeConnectorCoverage,
  KnowledgeConnectorRemoteLifecycle,
} from '../api/knowledge-connectors';
import './ConnectorCoverage.css';

type ConnectorCoverageProps = {
  coverage?: KnowledgeConnectorCoverage;
};

function percentage(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value * 100)));
}

function reasonLabel(reason?: string): string {
  return reason || '当前连接器版本暂时无法读取';
}

function hasLifecycleActivity(lifecycle?: KnowledgeConnectorRemoteLifecycle): boolean {
  if (!lifecycle || lifecycle.status === 'NOT_AVAILABLE' || lifecycle.status === 'UNKNOWN') return false;
  return Boolean(
    lifecycle.absent_count
    || lifecycle.unconfirmed_missing_count
    || lifecycle.retirement_eligible_count
    || lifecycle.retired_count
    || lifecycle.renamed_resource_count
    || lifecycle.moved_resource_count
    || lifecycle.reappeared_resource_count
    || lifecycle.evidence_persistence_status === 'FAILED',
  );
}

function ConnectorRemoteLifecycle({ lifecycle }: { lifecycle?: KnowledgeConnectorRemoteLifecycle }) {
  if (!hasLifecycleActivity(lifecycle) || !lifecycle) return null;

  const evidenceIncomplete = lifecycle.evidence_persistence_status === 'FAILED'
    || lifecycle.sync_receipt_persisted === false;
  return (
    <div className="connector-remote-lifecycle" aria-label="在线资料远端状态摘要">
      <div className="connector-remote-lifecycle-title">
        <strong>远端状态核验</strong>
        <span>仅根据当前配置范围内的完整快照判断</span>
      </div>

      {lifecycle.unconfirmed_missing_count > 0 && (
        <p>
          {lifecycle.unconfirmed_missing_count} 份资料本次未在配置范围内发现，系统仍保留原资料并继续核验；
          单次未发现不用于判断远端原因。
        </p>
      )}
      {lifecycle.retirement_eligible_count > 0 && lifecycle.retired_count === 0 && (
        <p>
          {lifecycle.retirement_eligible_count} 份资料已连续多次未在配置范围内发现，但尚未改变内部使用状态。
        </p>
      )}
      {lifecycle.retired_count > 0 && (
        <p>
          {lifecycle.retired_count} 份资料连续多次未在配置范围内发现，已停止作为最新资料使用；
          历史内容和证据仍完整保留。
        </p>
      )}

      {(lifecycle.renamed_resource_count > 0
        || lifecycle.moved_resource_count > 0
        || lifecycle.reappeared_resource_count > 0) && (
        <div className="connector-remote-lifecycle-events">
          {lifecycle.renamed_resource_count > 0 && (
            <span>已重命名 {lifecycle.renamed_resource_count}</span>
          )}
          {lifecycle.moved_resource_count > 0 && (
            <span>范围内移动 {lifecycle.moved_resource_count}</span>
          )}
          {lifecycle.reappeared_resource_count > 0 && (
            <span>重新出现并恢复 {lifecycle.reappeared_resource_count}</span>
          )}
        </div>
      )}

      {evidenceIncomplete && (
        <p className="connector-remote-lifecycle-warning">
          本次远端状态证据未完整写入同步收据，系统已保留原资料并将在后续同步中重新核验。
        </p>
      )}
      <small>
        系统不会根据一次缺失推断远端原因，也不会修改原资料。
      </small>
    </div>
  );
}

export function ConnectorCoverage({ coverage }: ConnectorCoverageProps) {
  if (!coverage || coverage.status === 'NOT_AVAILABLE') return null;

  const unknown = coverage.status === 'UNKNOWN';
  const partial = coverage.unsupported_count > 0;
  const progress = percentage(coverage.coverage_ratio);
  const statusLabel = unknown
    ? '覆盖状态待恢复'
    : partial
      ? `已读取 ${coverage.covered_count}/${coverage.discovered_count}`
      : `已完整读取 ${coverage.covered_count} 份`;

  return (
    <section
      className={`connector-coverage${partial || unknown ? ' connector-coverage-partial' : ''}`}
      aria-label="在线资料覆盖状态"
    >
      <div className="connector-coverage-heading">
        <div>
          <span>资料覆盖</span>
          <strong>{statusLabel}</strong>
        </div>
        {!unknown && <b>{progress}%</b>}
      </div>

      {!unknown && (
        <div
          className="connector-coverage-track"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
          aria-label={`在线资料读取覆盖率 ${progress}%`}
        >
          <span style={{ width: `${progress}%` }} />
        </div>
      )}

      {unknown ? (
        <p>最近一次同步收据暂不可用，系统会自动恢复；已有资料不会被覆盖或删除。</p>
      ) : partial ? (
        <>
          <p>
            已发现 {coverage.discovered_count} 份资料，其中 {coverage.covered_count} 份已进入企业知识库，
            {coverage.unsupported_count} 份资料类型暂不支持。其余资料仍可正常用于分析和测试，系统不会修改原资料。
          </p>
          <details className="connector-coverage-details">
            <summary>查看暂不支持的资料（{coverage.unsupported_count}）</summary>
            <div className="connector-coverage-list">
              {coverage.unsupported_resources.map((resource) => (
                <article key={`${resource.resource_index ?? resource.display_title ?? 'resource'}:${resource.resource_kind || ''}`}>
                  <div>
                    <strong>{resource.display_title || '未命名资料'}</strong>
                    <span>{resource.remote_object_type || resource.resource_kind || '未知类型'}</span>
                  </div>
                  <p>{reasonLabel(resource.reason_code)}</p>
                </article>
              ))}
              {coverage.unsupported_resources.length === 0 && (
                <p>缺口数量已记录，详细清单将在下一次同步后恢复。</p>
              )}
              {coverage.unsupported_resources_truncated && (
                <p>清单较长，当前仅展示前 100 份。</p>
              )}
            </div>
          </details>
        </>
      ) : (
        <p>本次发现的在线资料均已读取，没有已知资料类型缺口；系统不会修改原资料。</p>
      )}

      <ConnectorRemoteLifecycle lifecycle={coverage.remote_lifecycle} />
    </section>
  );
}

export default ConnectorCoverage;
