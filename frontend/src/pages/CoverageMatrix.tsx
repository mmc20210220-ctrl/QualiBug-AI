import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { runRegression } from '../api/client';
import { usePipelineData } from '../api/data';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { TermHint } from '../components/TermHint';
import { GLOSSARY } from '../lib/glossary';
import { asNum, asRecord, asText } from '../lib/value-guards';

type JsonRecord = Record<string, unknown>;
type CoverageNextAction = { title: string; label: string; path?: string; runRegression?: boolean };

function finiteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function pct(value: unknown): string {
  const parsed = finiteNumber(value);
  return parsed == null ? '未上报' : `${Math.round(parsed * 100)}%`;
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

function steeringStatusLabel(status: string, reason: string): string {
  if (status === 'applied') return '已按缺口调度';
  if (status === 'not_applied') return reason ? `未调度：${reason}` : '未启用调度';
  return status || '未上报';
}

function steeringTone(status: string): string {
  if (status === 'applied') return 'success';
  if (status === 'not_applied') return 'warning';
  return 'neutral';
}

function regressionRefreshLabel(status: string, reason: string): string {
  if (status === 'refreshed') return '已刷新回归套件';
  if (status === 'skipped') return reason ? `未刷新：${reason}` : '未刷新';
  if (status === 'refresh_failed') return reason ? `刷新失败：${reason}` : '刷新失败';
  return status || '未上报';
}

function regressionRefreshTone(status: string): string {
  if (status === 'refreshed') return 'success';
  if (status === 'refresh_failed') return 'danger';
  if (status === 'skipped') return 'warning';
  return 'neutral';
}

function regressionRunLabel(gateStatus: string, fallbackStatus: string): string {
  const status = gateStatus || fallbackStatus;
  if (status === 'passed') return '最近回归通过';
  if (status === 'failed') return '最近回归失败';
  if (status === 'manual_approval_required') return '最近回归需复核';
  if (status === 'available') return '最近回归已执行';
  return status || '暂无回归执行';
}

function regressionRunTone(gateStatus: string): string {
  if (gateStatus === 'passed') return 'success';
  if (gateStatus === 'failed') return 'danger';
  if (gateStatus === 'manual_approval_required') return 'warning';
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

function CoverageBar({ value }: { value: unknown }) {
  const parsed = finiteNumber(value);
  const clamped = parsed == null ? 0 : Math.max(0, Math.min(1, parsed));
  const percent = Math.round(clamped * 100);
  return (
    <div
      role="progressbar"
      aria-label={parsed == null ? '覆盖率未上报' : `覆盖率 ${percent}%`}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={parsed == null ? undefined : percent}
      style={{ height: 8, background: '#e2e8f0', borderRadius: 999, overflow: 'hidden' }}
    >
      <div style={{ width: `${percent}%`, height: '100%', background: clamped >= 0.8 ? '#16a34a' : clamped >= 0.4 ? '#d97706' : '#dc2626' }} />
    </div>
  );
}

export function CoverageMatrix() {
  usePageTitle('覆盖矩阵');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { data, loading, error, refetch } = usePipelineData(project);
  const { navigateToProjectPath } = useProjectNavigation();
  const toast = useToast();
  const [regressionRunning, setRegressionRunning] = useState(false);

  const handleRunRegression = async () => {
    if (!project || regressionRunning) return;
    if (!canRunRegression) {
      toast.show('当前没有可执行的回归义务；先形成回归探针后再运行回归。', 'warning');
      return;
    }
    setRegressionRunning(true);
    try {
      const result = await runRegression(project, { mode: 'release' });
      if (result.ok === false) {
        throw new Error(result.message || result.error || '回归执行失败');
      }
      const ci = asRecord(result.ci_feedback);
      const gate = asText(ci.gate_status);
      const summary = asRecord(result.summary);
      const failed = asNum(summary.failed_count);
      const review = asNum(summary.needs_review_count);
      if (gate === 'passed') {
        toast.show('回归执行完成：全部通过。', 'success');
      } else if (gate === 'failed' || failed > 0) {
        toast.show(`回归执行完成：${failed} 个探针失败。`, 'danger');
      } else if (review > 0) {
        toast.show(`回归执行完成：${review} 个探针需要复核。`, 'warning');
      } else {
        toast.show('回归执行完成，正在刷新结果。', 'info');
      }
      await refetch();
    } catch (err) {
      toast.show(err instanceof Error ? err.message : '回归执行失败', 'danger');
    } finally {
      setRegressionRunning(false);
    }
  };

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
  const scanMeta = asRecord(record.scan_meta);
  const dataContract = asRecord(record.data_contract);
  const matrix = asRecord(record.coverage_matrix);
  const summary = asRecord(record.coverage_matrix_summary || matrix.summary);
  const contract = asRecord(dataContract.coverage_matrix);
  const steeringContract = asRecord(dataContract.coverage_steering);
  const regressionContract = asRecord(dataContract.regression_suite_refresh);
  const regressionRunContract = asRecord(dataContract.regression_run);
  const steering = asRecord(record.coverage_steering || scanMeta.coverage_steering);
  const steeringStatus = asText(steering.status);
  const steeringReason = asText(steering.reason);
  const steeringWeights = asRecord(steering.gap_family_weights);
  const topSteeredIds = Array.isArray(steering.top_steered_slice_ids) ? steering.top_steered_slice_ids.map(String).filter(Boolean) : [];
  const regressionRefresh = asRecord(record.regression_suite_refresh || scanMeta.regression_suite_refresh);
  const regressionSummary = asRecord(regressionRefresh.summary || record.regression_suite);
  const regressionStatus = asText(regressionRefresh.status);
  const regressionReason = asText(regressionRefresh.reason);
  const regressionProbeCount = asNum(regressionSummary.total_probe_count);
  const canRunRegression = regressionProbeCount > 0;
  const regressionRun = asRecord(record.regression_run || scanMeta.regression_run);
  const regressionRunStatus = asText(regressionRun.status);
  const regressionGateStatus = asText(regressionRun.gate_status);
  const regressionFailed = regressionGateStatus === 'failed';
  const regressionFailures = Array.isArray(regressionRun.failures) ? regressionRun.failures.map(asRecord) : [];
  const regressionNeedsReview = Array.isArray(regressionRun.needs_review) ? regressionRun.needs_review.map(asRecord) : [];
  const hasRegressionRun = Boolean(regressionRunStatus || regressionGateStatus);
  const families = normalizeFamilyEntries(matrix).sort(([, a], [, b]) => asNum(a.coverage_rate) - asNum(b.coverage_rate));
  const invariants = normalizeInvariantEntries(matrix).sort(([, a], [, b]) => asNum(a.coverage_rate) - asNum(b.coverage_rate));
  const gaps = Array.isArray(record.coverage_gaps) ? record.coverage_gaps.map(asRecord).filter((item) => asText(item.kind) === 'RISK_FAMILY_COVERAGE_GAP') : [];
  const benchmark = asRecord(scanMeta.benchmark_metrics);
  const externalEvaluation = asRecord(
    Object.keys(asRecord(record.external_evaluation)).length > 0
      ? record.external_evaluation
      : scanMeta.external_evaluation,
  );
  const qualityClaimStatus = asText(record.quality_claim_status) || asText(scanMeta.quality_claim_status) || asText(externalEvaluation.measurement_status) || 'NOT_MEASURED';
  const externalMeasured = qualityClaimStatus === 'MEASURED' && asText(externalEvaluation.measurement_status) === 'MEASURED';
  const qualitySuppressed = !externalMeasured || Boolean(asRecord(externalEvaluation.display).suppress_quality_score) || Boolean(benchmark.commercial_quality_suppressed);
  const benchmarkActive = Boolean(benchmark.benchmark_active && benchmark.ground_truth_available) && !qualitySuppressed;
  const nextAction: CoverageNextAction = regressionFailed
    ? { title: '先处理最新回归失败，再判断发布', label: '查看问题清单', path: '/findings' }
    : gaps.length > 0
      ? { title: `继续关闭 ${gaps.length} 个风险家族缺口`, label: '继续检测剩余范围', path: '/campaigns' }
      : canRunRegression && regressionGateStatus !== 'passed'
        ? { title: '覆盖信息已形成，完成修复后回归验证', label: '运行回归验证', runRegression: true }
        : { title: '覆盖与回归状态已形成', label: '查看发布门禁', path: '/release' };

  if (families.length === 0 && invariants.length === 0 && !steeringStatus && !regressionStatus && !hasRegressionRun) {
    return (
      <section className="state-panel">
        <div className="state-panel-badge">覆盖矩阵</div>
        <h2>当前尚未生成风险覆盖矩阵</h2>
        <p>运行一次标准扫描后，系统会根据真实 findings/candidates 生成风险家族与业务不变量覆盖矩阵。</p>
        <div className="state-panel-actions">
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/campaigns', project)}>启动标准扫描</button>
          <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/settings', project)}>检查接入条件</button>
        </div>
      </section>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{asText(record.project_name) || project} · 风险覆盖矩阵</h1>
          <p>这里展示的是风险家族与业务不变量覆盖，不是 bug 召回率。只有接入 seeded ground truth 时，Benchmark 才能计算 recall / precision。</p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn btn-secondary" onClick={refetch} disabled={regressionRunning}>刷新矩阵</button>
          <button
            className="btn btn-secondary"
            onClick={handleRunRegression}
            disabled={regressionRunning || !canRunRegression}
            title={canRunRegression ? '运行当前已生成的 release 回归探针' : '当前没有可执行的回归义务'}
          >
            {regressionRunning ? '回归执行中…' : canRunRegression ? '运行回归验证' : '暂无回归义务'}
          </button>
        </div>
      </div>

      <div className="action-bar mb-4">
        <span className="action-bar-title">下一步：{nextAction.title}</span>
        {nextAction.runRegression ? (
          <button className="btn btn-primary" onClick={handleRunRegression} disabled={regressionRunning || !canRunRegression}>
            {regressionRunning ? '回归执行中…' : nextAction.label}
          </button>
        ) : (
          <button className="btn btn-primary" onClick={() => navigateToProjectPath(nextAction.path || '/dashboard', project)}>{nextAction.label}</button>
        )}
        {gaps.length > 0 && <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/settings', project)}>检查资料与环境</button>}
        <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回价值总览</button>
      </div>

      <div className="customer-summary-grid mb-4">
        <article className="customer-summary-card tone-primary"><span>风险家族覆盖</span><strong>{pct(summary.family_coverage_rate)}</strong><small>{asNum(summary.covered_family_count)} / {asNum(summary.ontology_family_count, families.length)} 个家族已触达</small></article>
        <article className="customer-summary-card tone-success"><span>确认覆盖</span><strong>{pct(summary.confirmed_family_rate)}</strong><small>{asNum(summary.confirmed_family_count)} 个家族已有 confirmed 证据</small></article>
        <article className="customer-summary-card tone-danger"><span><TermHint label="覆盖缺口" hint={GLOSSARY.coverageGap} /></span><strong>{asNum(summary.gap_family_count, gaps.length)}</strong><small>缺口不等于无风险，需要补资料/账号/环境/测试数据</small></article>
        <article className={`customer-summary-card tone-${steeringTone(steeringStatus)}`}><span>缺口调度</span><strong>{steeringStatus === 'applied' ? '已启用' : '待启用'}</strong><small>{steeringStatus ? steeringStatusLabel(steeringStatus, steeringReason) : '暂无调度诊断'}</small></article>
        <article className={`customer-summary-card tone-${regressionRefreshTone(regressionStatus)}`}><span><TermHint label="回归义务" hint={GLOSSARY.regressionObligation} /></span><strong>{regressionStatus === 'refreshed' ? regressionProbeCount : '待生成'}</strong><small>{regressionStatus ? regressionRefreshLabel(regressionStatus, regressionReason) : '暂无回归刷新诊断'}</small></article>
        <article className={`customer-summary-card tone-${regressionRunTone(regressionGateStatus)}`}><span>最近回归</span><strong>{hasRegressionRun ? regressionRunLabel(regressionGateStatus, regressionRunStatus) : '未执行'}</strong><small>{hasRegressionRun ? `通过 ${asNum(regressionRun.passed_count)} · 失败 ${asNum(regressionRun.failed_count)} · 复核 ${asNum(regressionRun.needs_review_count)}` : '形成回归义务后可执行回归验证'}</small></article>
        <article className={`customer-summary-card ${benchmarkActive ? 'tone-success' : 'tone-warning'}`}><span><TermHint label="外部质量评测" hint={GLOSSARY.externalEvaluation} /></span><strong>{qualitySuppressed ? '尚未评测' : (benchmarkActive ? '已启用' : '未启用')}</strong><small>{qualitySuppressed ? (asText(asRecord(externalEvaluation.display).quality_label) || '尚未完成外部质量评测；不显示召回/精度') : (benchmarkActive ? `召回率 ${pct(benchmark.recall)} · 精度 ${pct(benchmark.precision)}` : '当前矩阵不能当作召回率或商业能力证明')}</small></article>
      </div>

      <section className="customer-value-grid mb-4">
        <article className="customer-value-card"><span className="customer-value-kicker">诚实边界</span><h2>覆盖矩阵 ≠ 召回率</h2><p>{asText(summary.honesty_note) || asText(contract.honesty_rule) || '该矩阵来自真实扫描输出，用于说明哪些风险家族被触达、确认或仍是缺口；没有 ground truth 时不能计算 recall。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">客户下一步</span><h2>{gaps.length > 0 ? `优先关闭 ${gaps.length} 个风险家族缺口` : '当前风险家族已有覆盖信号'}</h2><p>{gaps.length > 0 ? '优先补齐对应业务资料、接口规范、多角色账号、租户数据、测试数据和执行授权，再运行下一轮 Campaign。' : '继续查看 confirmed 缺陷和回归状态，确认是否可以进入整改和验收闭环。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">调度反馈</span><h2>{steeringStatus === 'applied' ? `已优先调度 ${asNum(steering.steered_slice_count)} 个 slice` : steeringStatus ? steeringStatusLabel(steeringStatus, steeringReason) : '等待下一轮调度诊断'}</h2><p>{asText(steering.honesty_rule) || asText(steeringContract.honesty_rule) || '调度反馈只说明系统如何重排行为 slice，不代表发现了新 bug，也不会生成 synthetic coverage。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">修复后回归</span><h2>{regressionStatus === 'refreshed' ? `已沉淀 ${regressionProbeCount} 个探针` : regressionStatus ? regressionRefreshLabel(regressionStatus, regressionReason) : '等待回归套件刷新'}</h2><p>{asText(regressionRefresh.honesty_rule) || asText(regressionContract.honesty_rule) || '回归套件刷新只把已有证据支持的 confirmed 缺陷变成后续发布前必须执行的探针，不代表修复已经通过。'}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">最新回归结果</span><h2>{hasRegressionRun ? regressionRunLabel(regressionGateStatus, regressionRunStatus) : '尚未执行回归'}</h2><p>{hasRegressionRun ? (asText(regressionRun.ci_message) || `最近一次回归：通过 ${asNum(regressionRun.passed_count)}，失败 ${asNum(regressionRun.failed_count)}，需复核 ${asNum(regressionRun.needs_review_count)}。`) : (asText(regressionRunContract.honesty_rule) || '形成真实回归探针后，客户可以运行回归并在这里查看最新 persisted regression run verdict。')}</p></article>
        <article className="customer-value-card"><span className="customer-value-kicker">度量口径</span><h2>{asNum(summary.ontology_invariant_count, invariants.length)} 个不变量基线</h2><p>系统按业务不变量而不是固定 20 种 bug type 观察覆盖面。confirmed 需要真实请求、响应、断言和证据链支撑。</p></article>
      </section>

      {(steeringStatus || regressionStatus || hasRegressionRun) && (
        <section className="customer-secondary-grid mb-4">
          {steeringStatus && (
            <article className="customer-secondary-card">
              <span className="customer-value-kicker">覆盖缺口驱动调度</span>
              <h3>{steeringStatusLabel(steeringStatus, steeringReason)}</h3>
              <p>{steeringStatus === 'applied' ? '本轮调度已经根据 coverage matrix 的 gap/candidate_only 家族重新排序，优先尝试关闭高价值覆盖缺口。' : '当前没有可行动的覆盖缺口调度，可能是尚未生成矩阵，或没有 slice 能匹配缺口家族。'}</p>
              <div className="customer-secondary-meta">
                <span><em>调度分片</em><b>{asNum(steering.steered_slice_count)}</b></span>
                <span><em>原因</em><b>{steeringReason || '未上报'}</b></span>
                <span><em>优先分片</em><b>{topSteeredIds[0] || '暂无'}</b></span>
              </div>
              {Object.keys(steeringWeights).length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
                  {Object.entries(steeringWeights).map(([family, weight]) => (
                    <span key={family} className="summary-pill tone-warning">{family.replace(/_/g, ' ')} · 权重 {String(weight)}</span>
                  ))}
                </div>
              )}
              {topSteeredIds.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
                  {topSteeredIds.map((sliceId) => <span key={sliceId} className="summary-pill tone-success">{sliceId}</span>)}
                </div>
              )}
            </article>
          )}

          {regressionStatus && (
            <article className="customer-secondary-card">
              <span className="customer-value-kicker">Confirmed Bug 回归义务</span>
              <h3>{regressionRefreshLabel(regressionStatus, regressionReason)}</h3>
              <p>{regressionStatus === 'refreshed' ? '扫描后已自动刷新 smoke / release / full 回归套件；客户修复后可以直接运行回归验证这些 confirmed bug 是否复现。' : '当前没有形成新的 confirmed bug 回归义务，或回归套件刷新被跳过。'}</p>
              <div className="customer-secondary-meta">
                <span><em>回归探针</em><b>{regressionProbeCount}</b></span>
                <span><em>确认缺陷台账</em><b>{asNum(regressionSummary.confirmed_ledger_probe_count)}</b></span>
                <span><em>发布套件</em><b>{asNum(regressionSummary.release_count)}</b></span>
                <span><em>CI 门禁建议</em><b>{asText(regressionSummary.ci_gate_recommendation) || '未上报'}</b></span>
              </div>
            </article>
          )}

          {hasRegressionRun && (
            <article className="customer-secondary-card">
              <span className="customer-value-kicker">最近一次回归执行</span>
              <h3>{regressionRunLabel(regressionGateStatus, regressionRunStatus)}</h3>
              <p>{asText(regressionRun.honesty_rule) || '这里展示的是最新持久化的 regression_run_result，不会重新执行探针，也不会改写 verdict。'}</p>
              <div className="customer-secondary-meta">
                <span><em>套件模式</em><b>{asText(regressionRun.suite_mode_label) || asText(regressionRun.suite_mode) || '未上报'}</b></span>
                <span><em>通过</em><b>{asNum(regressionRun.passed_count)}</b></span>
                <span><em>失败</em><b>{asNum(regressionRun.failed_count)}</b></span>
                <span><em>需复核</em><b>{asNum(regressionRun.needs_review_count)}</b></span>
                <span><em>历史执行</em><b>{asNum(regressionRun.history_size)}</b></span>
              </div>
              {regressionFailures.length > 0 && (
                <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
                  {regressionFailures.slice(0, 5).map((failure, index) => (
                    <div key={`${asText(failure.regression_probe_id)}-${index}`} style={{ padding: '10px 12px', border: '1px solid var(--border-color, #e2e8f0)', borderRadius: 10, background: '#fff7ed' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 4 }}>
                        <strong>{asText(failure.title) || asText(failure.regression_probe_id) || '失败探针'}</strong>
                        <span className="summary-pill tone-danger">failed</span>
                      </div>
                      <p style={{ margin: 0 }}>{asText(failure.reason) || '回归失败，需查看证据。'}</p>
                      <div className="customer-secondary-meta" style={{ marginTop: 8 }}><span><em>接口</em><b>{asText(failure.method)} {asText(failure.path)}</b></span><span><em>HTTP</em><b>{String(failure.status_code || '未上报')}</b></span></div>
                    </div>
                  ))}
                </div>
              )}
              {regressionFailures.length === 0 && regressionNeedsReview.length > 0 && (
                <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
                  {regressionNeedsReview.slice(0, 5).map((item, index) => (
                    <span key={`${asText(item.regression_probe_id)}-${index}`} className="summary-pill tone-warning">{asText(item.title) || asText(item.regression_probe_id) || '需复核探针'}</span>
                  ))}
                </div>
              )}
            </article>
          )}
        </section>
      )}

      <section className="customer-secondary-grid mb-4">
        <article className="customer-secondary-card">
          <span className="customer-value-kicker">风险家族覆盖</span>
          <h3>{families.length} 个风险家族</h3>
          <p>按覆盖率从低到高排序，优先暴露当前最需要补资料或补执行条件的方向。</p>
          <div style={{ display: 'grid', gap: 10 }}>
            {families.map(([family, item]) => {
              const status = asText(item.coverage_status);
              return (
                <div key={family} style={{ padding: '10px 12px', border: '1px solid var(--border-color, #e2e8f0)', borderRadius: 10, background: '#f8fafc' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 6 }}>
                    <strong>{asText(item.display_name) || family.replace(/_/g, ' ')}</strong>
                    <span className={`summary-pill tone-${statusTone(status)}`}>{statusLabel(status)}</span>
                  </div>
                  <CoverageBar value={item.coverage_rate} />
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