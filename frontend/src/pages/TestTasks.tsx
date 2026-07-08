import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTestTaskBoard } from '../api/data';
import { usePageTitle } from '../lib/page-title';

type TaskStatus = 'pending' | 'running' | 'passed' | 'failed' | 'blocked';

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: '待执行',
  running: '执行中',
  passed: '已通过',
  failed: '未通过',
  blocked: '已阻断',
};

export function TestTasks() {
  usePageTitle('测试任务看板');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { board, loading, error } = useTestTaskBoard(project);

  const stats = useMemo(() => {
    const base: Record<TaskStatus, number> = { pending: 0, running: 0, passed: 0, failed: 0, blocked: 0 };
    if (!board) return base;
    for (const slice of board.slices) {
      const status = (slice.status || board.ledger.slice_status?.[slice.slice_id] || 'pending') as TaskStatus;
      if (status in base) base[status] += 1;
    }
    return base;
  }, [board]);

  const total = board?.slices.length ?? 0;
  const safetyBlocked = board?.execution.production_data_blocked ?? 0;
  const evidenceSaved = board?.evidence_chains_saved ?? 0;

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">主链闭环</span>
          <h1>测试任务看板</h1>
          <p>把测试任务规划（主链 4）的每一个可执行任务及其生命周期状态、生产数据禁触拦截（主链 5/6）与证据链采集（主链 7）统一展示，全程由后端单一真相源驱动。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">任务 {total}</span>
            <span className="summary-pill">已通过 {stats.passed}</span>
            <span className="summary-pill">执行中 {stats.running}</span>
            <span className="summary-pill">待执行 {stats.pending}</span>
            <span className="summary-pill">已阻断 {stats.blocked}</span>
          </div>
        </div>
      </div>

      {loading && (
        <section className="findings-empty-state compact">
          <div className="spinner spinner-centered" />
          <p>正在构建测试任务看板...</p>
        </section>
      )}

      {error && !loading && (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">加载失败</span>
          <p>{error}</p>
        </section>
      )}

      {!loading && !error && !board && (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">当前空态</span>
          <h3>暂未生成测试任务看板</h3>
          <p>{project ? '当前项目还没有生成行为路径计划。完成一次真实扫描后，这里会自动形成任务生命周期看板。' : '未选择项目，暂无测试任务数据。'}</p>
        </section>
      )}

      {!loading && board && (
        <>
          <div className="behavior-stat-grid mb-4">
            {([
              { label: '任务总数', val: total, tone: 'tone-api' },
              { label: '已通过', val: stats.passed, tone: 'tone-success' },
              { label: '执行中', val: stats.running, tone: 'tone-warning' },
              { label: '待执行', val: stats.pending, tone: 'tone-flow' },
              { label: '已阻断', val: stats.blocked, tone: 'tone-danger' },
              { label: '生产数据禁触拦截', val: safetyBlocked, tone: 'tone-danger' },
              { label: '已落盘证据链', val: evidenceSaved, tone: 'tone-doc' },
            ] as Array<{ label: string; val: number; tone: string }>).map((m) => (
              <article key={m.label} className={`behavior-stat-card ${m.tone}`}>
                <strong>{m.val}</strong>
                <span>{m.label}</span>
              </article>
            ))}
          </div>

          <div className="behavior-matrix-panel">
            <div className="behavior-matrix-head">
              <div>
                <span className="panel-kicker">任务明细</span>
                <h2>测试任务生命周期</h2>
              </div>
              {board.ledger.campaign_id && (
                <div className="coverage-header-meta">
                  Campaign {board.ledger.campaign_id}
                  {board.ledger.campaign_status ? ` · ${board.ledger.campaign_status}` : ''}
                </div>
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>任务 ID</th>
                    <th>业务实体</th>
                    <th>类型</th>
                    <th>优先级</th>
                    <th>覆盖端点</th>
                    <th className="text-center">生命周期状态</th>
                  </tr>
                </thead>
                <tbody>
                  {board.slices.map((slice) => {
                    const status = (slice.status || board.ledger.slice_status?.[slice.slice_id] || 'pending') as TaskStatus;
                    return (
                      <tr key={slice.slice_id}>
                        <td className="font-mono behavior-matrix-code">{slice.slice_id}</td>
                        <td>{slice.entity || '—'}</td>
                        <td>{slice.kind || '—'}</td>
                        <td>{slice.priority || '—'}</td>
                        <td className="behavior-matrix-detail">
                          {slice.endpoints && slice.endpoints.length > 0
                            ? slice.endpoints.map((ep, i) => (
                                <span key={i} className="behavior-endpoint-chip">{ep}</span>
                              ))
                            : '—'}
                        </td>
                        <td className="text-center">
                          <span className={`status task-status-${status}`}>{STATUS_LABEL[status]}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default TestTasks;
