import type { ExpectedActualComparison } from '../../types';

interface AssertionDiffProps {
  comparison: ExpectedActualComparison;
  expected?: string;
  actual?: string;
}

export function AssertionDiff({ comparison, expected, actual }: AssertionDiffProps) {
  const exp = comparison?.expected || expected || '未指定';
  const act = comparison?.actual || actual || '未捕获';
  const diff = comparison?.difference || '';

  return (
    <div className="assertion-diff">
      <div className="assertion-diff-row">
        <span className="assertion-diff-label expected">预期</span>
        <span className="assertion-diff-value">{exp}</span>
      </div>
      <div className="assertion-diff-row">
        <span className="assertion-diff-label actual">实际</span>
        <span className="assertion-diff-value">{act}</span>
      </div>
      {diff && (
        <div className="assertion-diff-row">
          <span className="assertion-diff-label" style={{ color: 'var(--warning)' }}>差异</span>
          <span className="assertion-diff-value">{diff}</span>
        </div>
      )}
      {comparison?.db_comparison && (
        <div className="assertion-diff-row">
          <span className="assertion-diff-label" style={{ color: 'var(--primary)' }}>DB</span>
          <span className="assertion-diff-value">
            {comparison.db_comparison.table}.{comparison.db_comparison.column}: 预期 {comparison.db_comparison.expected} → 实际 {comparison.db_comparison.actual}
          </span>
        </div>
      )}
    </div>
  );
}
