import type { KnowledgeConnectorCoverage } from '../api/knowledge-connectors';
import './ConnectorCoverage.css';

type ConnectorCoverageProps = {
  coverage?: KnowledgeConnectorCoverage;
};

function percentage(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value * 100)));
}

function reasonLabel(reason?: string): string {
  switch (reason) {
    case 'FEISHU_OBJECT_TYPE_UNSUPPORTED':
      return '当前版本暂不支持该飞书资料类型';
    default:
      return reason || '当前版本暂时无法读取';
  }
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
            {coverage.unsupported_count} 份资料类型暂不支持。其余资料仍可正常用于分析和测试。
          </p>
          <details className="connector-coverage-details">
            <summary>查看暂不支持的资料（{coverage.unsupported_count}）</summary>
            <div className="connector-coverage-list">
              {coverage.unsupported_resources.map((resource) => (
                <article key={`${resource.remote_resource_id}:${resource.resource_kind || ''}`}>
                  <div>
                    <strong>{resource.display_title || '未命名飞书资料'}</strong>
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
        <p>本次发现的在线资料均已读取，没有已知资料类型缺口。</p>
      )}
    </section>
  );
}

export default ConnectorCoverage;
