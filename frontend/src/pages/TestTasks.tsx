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

/** Color-coded dimension labels for business risk categories */
const DIM_LABELS: Record<string, { label: string; tone: string }> = {
  authorization_access_control: { label: '角色权限', tone: 'tone-danger' },
  permission_boundary: { label: '权限边界', tone: 'tone-danger' },
  tenant_isolation: { label: '租户隔离', tone: 'tone-danger' },
  tenant: { label: '租户隔离', tone: 'tone-danger' },
  money_quantity_conservation: { label: '金额守恒', tone: 'tone-warning' },
  money: { label: '金额守恒', tone: 'tone-warning' },
  quantity: { label: '库存守恒', tone: 'tone-warning' },
  conservation: { label: '守恒约束', tone: 'tone-warning' },
  data_conservation: { label: '守恒约束', tone: 'tone-warning' },
  state_machine: { label: '状态流转', tone: 'tone-flow' },
  lifecycle: { label: '状态流转', tone: 'tone-flow' },
  state: { label: '状态流转', tone: 'tone-flow' },
  audit_traceability: { label: '审计追溯', tone: 'tone-doc' },
  audit: { label: '审计追溯', tone: 'tone-doc' },
  cross_surface_consistency: { label: '跨面一致', tone: 'tone-api' },
  data_consistency: { label: '数据一致', tone: 'tone-api' },
  ui_api_contract: { label: 'UI/API契约', tone: 'tone-api' },
  idempotency: { label: '幂等性', tone: 'tone-warning' },
  async_eventual_consistency: { label: '异步一致', tone: 'tone-flow' },
  concurrency_race_condition: { label: '并发竞态', tone: 'tone-danger' },
  visibility_disclosure: { label: '可见性', tone: 'tone-flow' },
};

function normalizeDimKey(dim: string): string {
  return dim.toLowerCase().replace(/-/g, '_').replace(/ /g, '_');
}

function dimLabel(dim: string): { label: string; tone: string } {
  return DIM_LABELS[normalizeDimKey(dim)] || { label: dim, tone: 'tone-flow' };
}

function surfaceIcon(surface: string): string {
  const icons: Record<string, string> = {
    api: '🔗', db: '🗄️', ui: '🖥️', auth: '🔐', log: '📋', async: '⏳',
  };
  return icons[surface.toLowerCase()] || '📌';
}

export function TestTasks() {
  usePageTitle('测试任务看板');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { board, obligationProjection, loading, error } = useTestTaskBoard(project);

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

  // Compute dimension distribution for summary
  const dimSummary = useMemo(() => {
    const counts: Record<string, number> = {};
    if (!board) return counts;
    for (const slice of board.slices) {
      for (const dim of slice._system_behavior_dimensions || []) {
        const key = dimLabel(dim).label;
        counts[key] = (counts[key] || 0) + 1;
      }
    }
    return counts;
  }, [board]);

  // Count slices with steering signals
  const steeredCount = board?.slices.filter(
    (s) => (s._coverage_steering_weight || 0) > 0 || (s._learning_steering_weight || 0) > 0
  ).length ?? 0;
  const boundaryBoostedCount = board?.slices.filter(
    (s) => (s._historical_boundary_boost || 0) > 0
  ).length ?? 0;
  const oblTotal = Number(obligationProjection.obligation_total || 0);
  const oblCompiled = Number(obligationProjection.obligation_compiled || 0);
  const oblBlocked = Number(obligationProjection.obligation_blocked || 0);
  const oblExecuted = Number(obligationProjection.obligation_executed || 0);
  const formalDefects = Number(obligationProjection.formal_defect_count || 0);
  const blockReasons = (obligationProjection.block_reason_counts && typeof obligationProjection.block_reason_counts === 'object')
    ? Object.entries(obligationProjection.block_reason_counts as Record<string, unknown>).slice(0, 4)
    : [];
  const fingerprints = (obligationProjection.fingerprints && typeof obligationProjection.fingerprints === 'object')
    ? obligationProjection.fingerprints as Record<string, unknown>
    : {};
  const adapterHealth = (obligationProjection.adapter_health && typeof obligationProjection.adapter_health === 'object')
    ? obligationProjection.adapter_health as Record<string, unknown>
    : {};

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">主链闭环</span>
          <h1>测试任务看板</h1>
          <p>后端 System Behavior Space 的多维度业务风险验证任务，每个任务携带业务语义维度、证据面和优先级信号，全程由后端单一真相源驱动。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">任务 {total}</span>
            <span className="summary-pill">已通过 {stats.passed}</span>
            <span className="summary-pill">执行中 {stats.running}</span>
            <span className="summary-pill">待执行 {stats.pending}</span>
            <span className="summary-pill">已阻断 {stats.blocked}</span>
            {steeredCount > 0 && <span className="summary-pill">学习调度 {steeredCount}</span>}
            {boundaryBoostedCount > 0 && <span className="summary-pill">历史边界 {boundaryBoostedCount}</span>}
            {oblTotal > 0 && <span className="summary-pill strong">义务 {oblTotal}</span>}
            {oblTotal > 0 && <span className="summary-pill">已编译 {oblCompiled}</span>}
            {oblTotal > 0 && <span className="summary-pill">已执行 {oblExecuted}</span>}
            {oblBlocked > 0 && <span className="summary-pill">编译阻断 {oblBlocked}</span>}
            {formalDefects > 0 && <span className="summary-pill">正式缺陷 {formalDefects}</span>}
          </div>
          {(oblTotal > 0 || Object.keys(fingerprints).length > 0) && (
            <div className="page-summary-strip" style={{ marginTop: 8 }}>
              {Boolean(fingerprints.behavior_ir_model_id) && <span className="summary-pill">IR {String(fingerprints.behavior_ir_model_id).slice(0, 12)}</span>}
              {Boolean(fingerprints.source_hash) && <span className="summary-pill">source {String(fingerprints.source_hash).slice(0, 8)}</span>}
              {Boolean(fingerprints.policy_version) && <span className="summary-pill">policy {String(fingerprints.policy_version)}</span>}
              {Number(adapterHealth.blocked_count || 0) > 0 && <span className="summary-pill">adapter 阻断 {Number(adapterHealth.blocked_count)}</span>}
              {blockReasons.map(([reason, count]) => (
                <span key={reason} className="summary-pill">{reason} · {String(count)}</span>
              ))}
            </div>
          )}
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
              { label: '生产数据禁触', val: safetyBlocked, tone: 'tone-danger' },
              { label: '已落盘证据链', val: evidenceSaved, tone: 'tone-doc' },
              { label: '学习调度', val: steeredCount, tone: 'tone-api' },
              { label: '历史边界匹配', val: boundaryBoostedCount, tone: 'tone-warning' },
            ] as Array<{ label: string; val: number; tone: string }>).map((m) => (
              <article key={m.label} className={`behavior-stat-card ${m.tone}`}>
                <strong>{m.val}</strong>
                <span>{m.label}</span>
              </article>
            ))}
          </div>

          {/* Dimension distribution summary */}
          {Object.keys(dimSummary).length > 0 && (
            <div className="behavior-dim-summary mb-4">
              <span className="panel-kicker">业务维度分布</span>
              <div className="flex flex-wrap gap-2 mt-2">
                {Object.entries(dimSummary).sort((a, b) => b[1] - a[1]).map(([dim, count]) => (
                  <span key={dim} className="behavior-endpoint-chip tone-flow">
                    {dim} ×{count}
                  </span>
                ))}
              </div>
            </div>
          )}

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
                    <th>实体</th>
                    <th>类型</th>
                    <th>业务维度</th>
                    <th>证据面</th>
                    <th>覆盖端点</th>
                    <th>缺口</th>
                    <th>调度信号</th>
                    <th className="text-center">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {board.slices.map((slice) => {
                    const status = (slice.status || board.ledger.slice_status?.[slice.slice_id] || 'pending') as TaskStatus;
                    const dims = slice._system_behavior_dimensions || [];
                    const surfaces = slice._system_behavior_surface_plan || [];
                    const gaps = slice.evidence_gaps || [];
                    const steering = [
                      (slice._coverage_steering_weight || 0) > 0 ? 'C' : '',
                      (slice._learning_steering_weight || 0) > 0 ? 'L' : '',
                      (slice._historical_boundary_boost || 0) > 0 ? 'H' : '',
                    ].filter(Boolean).join('/');
                    return (
                      <tr key={slice.slice_id}>
                        <td className="font-medium">{slice.entity || slice.slice_id?.slice(0, 12) || '—'}</td>
                        <td>{slice.kind || '—'}</td>
                        <td>
                          <div className="flex flex-wrap gap-1">
                            {dims.length > 0
                              ? dims.map((dim) => {
                                  const { label, tone } = dimLabel(dim);
                                  return (
                                    <span key={dim} className={`behavior-endpoint-chip ${tone}`} title={dim}>
                                      {label}
                                    </span>
                                  );
                                })
                              : '—'}
                          </div>
                        </td>
                        <td>
                          <div className="flex flex-wrap gap-1">
                            {surfaces.length > 0
                              ? surfaces.map((s) => (
                                  <span key={s} className="behavior-endpoint-chip" title={s}>
                                    {surfaceIcon(s)} {s}
                                  </span>
                                ))
                              : '—'}
                          </div>
                        </td>
                        <td className="behavior-matrix-detail">
                          {slice.endpoints && slice.endpoints.length > 0
                            ? slice.endpoints.map((ep, i) => (
                                <span key={i} className="behavior-endpoint-chip">{ep}</span>
                              ))
                            : '—'}
                        </td>
                        <td>
                          {gaps.length > 0
                            ? gaps.slice(0, 2).map((g) => (
                                <span key={g} className="behavior-endpoint-chip tone-danger" title={g}>
                                  {g.length > 30 ? g.slice(0, 30) + '…' : g}
                                </span>
                              ))
                            : '—'}
                        </td>
                        <td className="text-center">
                          {steering || '—'}
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
