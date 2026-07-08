import { useSearchParams } from 'react-router-dom';
import { usePipelineData } from '../api/data';
import { usePageTitle } from '../lib/page-title';

type JsonRecord = Record<string, unknown>;

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function asNum(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function pct(value: unknown): string {
  return `${Math.round(asNum(value) * 100)}%`;
}

function statusLabel(status: string): string {
  if (status === 'confirmed_with_evidence') return '已确认且有证据';
  if (status === 'confirmed_needs_evidence') return '已确认但需补证';
  if (status === 'candidate_only') return '候选覆盖';
  if (status === 'gap') return '覆盖缺口';
  return status || '未上报';
}

function statusTone(status: string): string {
  if (status === 'confirmed_with_evidence') return 'success';
  if (status === 'confirmed_needs_evidence' || status === 'candidate_only') return 'warning';
  if (status === 'gap') return 'danger';
  return 'neutral';
}

function normalizeFamilyEntries(matrix: JsonRecord): Array<[string, JsonRecord]> {
  const mapped = asRecord(matrix.risk_family_coverage);
  if (Object.keys(mapped).length > 0) {
    return Object.entries(mapped).map(([family, value]) => [family, asRecord(value)] as [string, JsonRecord]);
  }
  const rows = Array.isArray(matrix.families) ? matrix.families : [];
  return rows.map(asRecord).filter((row) => asText(row.family)).map((row) => {
    const family = asText(row.family);
    const status = asText(row.coverage_status) || 'gap';
    const confirmed = asNum(row.confirmed_count);
    const candidates = asNum(row.candidate_count);
    const target = Math.max(1, asNum(row.target_invariant_count, 1));
    const covered = status === 'gap' ? 0 : Math.max(confirmed + candidates, asNum(row.touched_invariant_count), 1);
    return [family, {
      display_name: asText(row.display_name) || family,
      coverage_status: status,
      coverage_rate: covered / Math.max(target, covered, 1),
      execution_rate: confirmed / Math.max(target, covered, 1),
      covered_items: covered,
      total_items: Math.max(target, covered, 1),
      confirmed_count: confirmed,
      candidate_count: candidates,
      evidence_complete_count: asNum(row.evidence_complete_count),
    }] as [string, JsonRecord];
  });
}

function normalizeInvariantEntries(matrix: JsonRecord): Array<[string, JsonRecord]> {
  const mapped = asRecord(matrix.invariant_coverage);
  if (Object.keys(mapped).length > 0) {
    return Object.entries(mapped).map(([invariant, value]) => [invariant, asRecord(value)] as [string, JsonRecord]);
  }
  const rows = Array.isArray(matrix.invariants) ? matrix.invariants : [];
  return rows.map(asRecord).filter((row) => asText(row.invariant)).map((row) => {
    const invariant = asText(row.invariant);
    const status = asText(row.coverage_status) || 'gap';
    const confirmed = asNum(row.confirmed_count);
    const candidates = asNum(row.candidate_count);
    const covered = status === 'gap' ? 0 : Math.max(confirmed + candidates, 1);
    return [invariant, {
      family: asText(row.family),
      coverage_status: status,
      coverage_rate: covered ? 1 : 0,
      covered_items: covered,
      total_items: 1,
      confirmed_count: confirmed,
      candidate_count: candidates,
      evidence_complete_count: asNum(row.evidence_complete_count),
    }] as [string, JsonRecord];
  });
}

function CoverageBar({ value }: { value: number }) {
  const clamped = Math.max(0, Math.min(1, value));
  return (
    <div style={{ height: 8, background: '#e2e8f0', borderRadius: 999, overflow: 'hidden' }}>
      <div style={{ width: `${Math.max(4, Math.round(clamped * 100))}%`, height: '100%', background: clamped >= 0.8 ? '#16a34a' : clamped >= 0.4 ? '#d97706' : '#dc2626' }} />
    </div>
  );
}

export function CoverageMatrix() {
  usePageTitle('风险覆盖矩阵');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { data, loading, error, refetch } = usePipelineData(project);

  if (!project) {
    return <section className="state-panel"><div className="state-panel-badge">客户选择</div><h2>请先选择客户项目</h2><p>风险覆盖矩阵基于当前项目的 command center 结果生成。</p></section>;
  }
  if (loading) {
    return <section className="state-panel"><div className="state-panel-badge">加载中</div><h2>正在读取风险覆盖矩阵</h2><p>系统正在读取扫描后的风险家族与业务不变量覆盖结果。</p></section>;
  }
  if (error && !data) {
    return <section className="state-panel"><div className="state-panel-badge">连接状态</div><h2>后端暂时不可用</h2><p>{error}</p><div className="state-panel-actions"><button className="btn btn-primary" onClick={refetch}>重新连接</button></div></section>;
  }

  const record = asRecord(data);
  const matrix = asRecord(record.coverage_matrix);
  const summary = asRecord(record.coverage_matrix_summary || matrix.summary);
  const contract = asRecord(asRecord(record.data_contract).coverage_matrix);
  const families = normalizeFamilyEntries(matrix).sort(([, a], [, b]) => asNum(a.coverage_rate) - asNum(b.coverage_rate));
  const invariants = normalizeInvariantEntries(matrix).sort(([, a], [, b]) => asNum(a.coverage_rate) - asNum(b.coverage_rate));
  const gaps = Array.isArray(record.coverage_gaps) ? record.coverage_gaps.map(asRecord).filter((item) => asText(item.kind) === 'RISK_FAMILY_COVERAGE_GAP') : [];
  const benchmark = asRecord(asRecord(record.scan_meta).benchmark_metrics);
  const benchmarkActive = Boolean(benchmark.benchmark_active && benchmark.ground_truth_available);

  if (families.length === 0 && invariants.length === 0) {
    return <section className="state-panel"><div className="state-panel-badge">覆盖矩阵</div><h2>当前尚未生成风险覆盖矩阵</h2><p>运行一次标准扫描后，系统会根据真实 findings/candidates 生成风险家族与业务不变量覆盖矩阵。</p></section>;
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{asText(record.project_name) || project} · 风险覆盖矩阵</h1>
          <p>这里展示的是风险家族与业务不变量覆盖，不是 bug 召回率。只有接入 seeded ground truth 时，Benchmark 才能计算 recall / precision。</p>
        </div>
        <button className="btn btn-secondary" onClick={refetch}>刷新矩阵</button>
      </div>

      <div className="customer-summary-grid mb-4">
        <article className="customer-summary-card tone-primary"><span>风险家族覆盖</span><strong>{pct(summary.family_coverage_rate)}</strong><small>{asNum(summary.covered_family_count)} / {asNum(summary.ontology_family_count, families.length)} 个家族已触达</small></article>
        <article className="customer-summary-card tone-success"><span>确认覆盖</span><strong>{pct(summary.confirmed_family_rate)}</strong><small>{asNum(summary.confirmed_family_count)} 个家族已有 confirmed 证据</small></article>
        <article className="customer-summary-card tone-danger"><span>覆盖缺口</span><strong>{asNum(summary.gap_family_count, gaps.length)}</strong><small>缺口不等于无风险，需要补资料/账号/环境/测试数据</small></article>
        <article className={`customer-summary-card ${benchmarkActive ? 'tone-success' : 'tone-warning'}`}><span>Benchmark 状态</span><strong>{benchmarkActive ? '已启用' : '未启用'}</strong><small>{benchmarkActive ? `召回率 ${pct(benchmark.recall)} · 精度 ${pct(benchmark.precision)}` : '当前矩阵不能当作召回率或 99% 能力证明'}</small></article>
      </div>

      <section className="customer-value-grid mb-4">
        <article className="customer-value-card"><span className="customer-value-kicker">诚实边界</span><h2>覆盖矩阵 ≠ 召回率</h2><p>{asText(summary.honesty_note) || asText(contract.honesty_rule) || '该矩阵来自真实扫描输出，用于说明哪些风险家族被触达、确认或仍是缺口；没有 ground truth 时不能计算 recall。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">客户下一步</span><h2>{gaps.length > 0 ? `优先关闭 ${gaps.length} 个风险家族缺口` : '当前风险家族已有覆盖信号'}</h2><p>{gaps.length > 0 ? '优先补齐对应业务资料、接口规范、多角色账号、租户数据、测试数据和执行授权，再运行下一轮 Campaign。' : '继续查看 confirmed 缺陷和回归状态，确认是否可以进入整改和验收闭环。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">度量口径</span><h2>{asNum(summary.ontology_invariant_count, invariants.length)} 个不变量基线</h2><p>系统按业务不变量而不是固定 20 种 bug type 观察覆盖面。confirmed 需要真实请求、响应、断言和证据链支撑。</p></article>
      </section>

      <section className="customer-secondary-grid mb-4">
        <article className="customer-secondary-card">
          <span className="customer-value-kicker">风险家族覆盖</span>
          <h3>{families.length} 个风险家族</h3>
          <p>按覆盖率从低到高排序，优先暴露当前最需要补资料或补执行条件的方向。</p>
          <div style={{ display: 'grid', gap: 10 }}>
            {families.map(([family, item]) => {
              const status = asText(item.coverage_status);
              const rate = asNum(item.coverage_rate);
              return (
                <div key={family} style={{ padding: '10px 12px', border: '1px solid var(--border-color, #e2e8f0)', borderRadius: 10, background: '#f8fafc' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 6 }}>
                    <strong>{asText(item.display_name) || family.replace(/_/g, ' ')}</strong>
                    <span className={`summary-pill tone-${statusTone(status)}`}>{statusLabel(status)}</span>
                  </div>
                  <CoverageBar value={rate} />
                  <div className="customer-secondary-meta" style={{ marginTop: 8 }}>
                    <span><em>覆盖</em><b>{asNum(item.covered_items)} / {asNum(item.total_items)}</b></span>
                    <span><em>confirmed</em><b>{asNum(item.confirmed_count)}</b></span>
                    <span><em>candidate</em><b>{asNum(item.candidate_count)}</b></span>
                    <span><em>证据完整</em><b>{asNum(item.evidence_complete_count)}</b></span>
                  </div>
                </div>
              );
            })}
          </div>
        </article>

        <article className="customer-secondary-card">
          <span className="customer-value-kicker">业务不变量覆盖</span>
          <h3>{invariants.length} 个不变量</h3>
          <p>不变量是系统行为建模的核心，比固定 bug type 更容易扩展到陌生业务系统。</p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {invariants.slice(0, 80).map(([invariant, item]) => {
              const status = asText(item.coverage_status);
              return (
                <span key={invariant} className={`summary-pill tone-${statusTone(status)}`} title={statusLabel(status)}>
                  {invariant.replace(/_/g, ' ')} · {statusLabel(status)}
                </span>
              );
            })}
          </div>
        </article>
      </section>

      {gaps.length > 0 && (
        <section className="customer-focus-section mb-4">
          <div className="customer-section-head"><div><span className="panel-kicker">覆盖缺口</span><h2>下一轮最应该补齐的风险家族</h2></div></div>
          <div className="customer-focus-list">
            {gaps.slice(0, 8).map((gap, index) => (
              <article key={`${asText(gap.family)}-${index}`} className="customer-focus-card">
                <div className="customer-focus-head"><span className="severity p1">GAP</span><strong>{asText(gap.title) || asText(gap.family)}</strong></div>
                <p>{asText(gap.reason) || '该风险家族当前没有真实覆盖信号。'}</p>
                <div className="customer-focus-meta"><span><em>下一步</em><b>{asText(gap.next_action) || '补齐资料和执行条件后重跑 Campaign'}</b></span></div>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

export default CoverageMatrix;
