import { useSearchParams } from 'react-router-dom';
import { usePipelineData, useReleaseData } from '../api/data';
import { usePageTitle } from '../lib/page-title';

type JsonRecord = Record<string, unknown>;
type GateCheck = { name: string; status: 'pass' | 'fail' | 'pending'; detail: string };

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

function regressionGateCheck(record: JsonRecord): GateCheck | null {
  const regressionRun = asRecord(record.regression_run);
  const regressionSummary = asRecord(record.regression_summary);
  const latestRun = asRecord(regressionSummary.latest_run);
  const regressionRefresh = asRecord(record.regression_suite_refresh);
  const regressionSuite = asRecord(record.regression_suite);
  const refreshSummary = asRecord(regressionRefresh.summary);
  const gateStatus = asText(regressionRun.gate_status) || asText(latestRun.gate_status);
  const hasLatestRun = Boolean(gateStatus || asText(regressionRun.status) || asText(latestRun.generated_at));
  const obligationCount = asNum(regressionSuite.total_probe_count, asNum(refreshSummary.total_probe_count));
  const confirmedLedgerProbeCount = asNum(regressionSuite.confirmed_ledger_probe_count, asNum(refreshSummary.confirmed_ledger_probe_count));
  const failed = asNum(regressionRun.failed_count, asNum(regressionSummary.failed_defect_count));
  const needsReview = asNum(regressionRun.needs_review_count, asNum(regressionSummary.pending_defect_count));
  const passed = asNum(regressionRun.passed_count, asNum(regressionSummary.passed_defect_count));

  if (gateStatus === 'failed') {
    return {
      name: '修复后回归 Gate',
      status: 'fail',
      detail: `最近一次回归失败：${failed} 个探针失败，${needsReview} 个需复核。发布前必须先修复或复核失败项。`,
    };
  }
  if (gateStatus === 'manual_approval_required') {
    return {
      name: '修复后回归 Gate',
      status: 'pending',
      detail: `最近一次回归仍需人工复核：${needsReview} 个探针缺少强自动判定，不能直接放行发布。`,
    };
  }
  if (gateStatus === 'passed') {
    return {
      name: '修复后回归 Gate',
      status: 'pass',
      detail: `最近一次回归通过：${passed} 个探针通过。该结论仅代表最近一次持久化回归结果，不扩大到未覆盖范围。`,
    };
  }
  if (!hasLatestRun && asText(regressionRefresh.status) === 'refreshed' && obligationCount > 0) {
    return {
      name: '修复后回归 Gate',
      status: 'pending',
      detail: `已自动生成 ${obligationCount} 个回归探针，其中 ${confirmedLedgerProbeCount} 个来自 confirmed bug ledger；发布前必须先执行 Smoke 或 Release 回归。`,
    };
  }
  return null;
}

export function ReleaseGate() {
  usePageTitle('发布门禁');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { data, loading } = useReleaseData(project);
  const { data: pipelineData } = usePipelineData(project);
  const pipelineRecord = asRecord(pipelineData);
  const regressionCheck = regressionGateCheck(pipelineRecord);

  const baseChecks = (data?.checks || []) as GateCheck[];
  const checks = regressionCheck ? [regressionCheck, ...baseChecks] : baseChecks;
  const derivedOverall: 'pass' | 'fail' | 'pending' = checks.some(c => c.status === 'fail') ? 'fail' : checks.some(c => c.status === 'pending') ? 'pending' : 'pass';
  const overall = checks.length > 0 ? derivedOverall : (data?.overall || 'pass');
  const passCount = checks.filter(c => c.status === 'pass').length;
  const failCount = checks.filter(c => c.status === 'fail').length;
  const pendingCount = checks.filter(c => c.status === 'pending').length;
  const hasGateData = checks.length > 0;
  const gateMode: 'missing_project' | 'no_data' | 'data' = !project ? 'missing_project' : hasGateData ? 'data' : 'no_data';
  const gateTitle = gateMode === 'data' ? (overall === 'pass' ? '通过' : overall === 'pending' ? '待处理' : '阻塞') : gateMode === 'missing_project' ? '未选择项目' : '暂无数据';
  const gateSub = gateMode === 'data'
    ? `${passCount}/${checks.length} 检查通过${failCount > 0 ? `，${failCount} 项阻塞` : pendingCount > 0 ? `，${pendingCount} 项待处理` : ''}`
    : gateMode === 'missing_project'
      ? '请选择项目后生成门禁结果'
      : '运行一次完整扫描以生成发布门禁检查结果';
  const gateClass = gateMode === 'data' ? overall : 'pending';
  const summaryCards = [
    { label: '通过', value: gateMode === 'data' ? passCount : '--', tone: 'success' },
    { label: '阻塞', value: gateMode === 'data' ? failCount : '--', tone: 'danger' },
    { label: '待处理', value: gateMode === 'data' ? pendingCount : '--', tone: 'warning' },
  ];

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">发布评审</span>
          <h1>发布门禁</h1>
          <p>把检测结果沉淀为可执行的发布结论，先看是否可发，再看阻塞项与待闭环项。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">当前结论 {gateTitle}</span>
            <span className="summary-pill">通过 {gateMode === 'data' ? passCount : '--'}</span>
            <span className="summary-pill">阻塞 {gateMode === 'data' ? failCount : '--'}</span>
            <span className="summary-pill">待处理 {gateMode === 'data' ? pendingCount : '--'}</span>
          </div>
        </div>
      </div>

      <section className={`release-hero release-hero-${gateClass} mb-4`}>
        <div className="release-hero-main">
          <span className="release-hero-kicker">门禁结论</span>
          <h2>{gateTitle}</h2>
          <p>{gateSub}</p>
        </div>
        <div className="release-hero-side">
          <div className={`gate-result ${gateClass}`}>
            <div className="gate-result-copy">
              <h2>{gateTitle}</h2>
              <p>{gateSub}</p>
            </div>
          </div>
        </div>
      </section>

      {loading && (
        <div className="findings-empty-state compact">
          <div className="spinner spinner-centered" />
          <p>评估发布就绪状态...</p>
        </div>
      )}

      <div className="release-stat-grid mb-4">
        {summaryCards.map((item) => (
          <article key={item.label} className={`release-stat-card tone-${item.tone}`}>
            <strong>{item.value}</strong>
            <span>{item.label}</span>
          </article>
        ))}
      </div>

      <section className="release-summary-panel mb-4">
        <div className="release-summary-head">
          <div>
            <span className="panel-kicker">决策说明</span>
            <h2>当前发布判断</h2>
          </div>
        </div>
        <div className="release-summary-grid">
          <div className="release-summary-card">
            <strong>是否可发布</strong>
            <p>
              {gateMode === 'data'
                ? overall === 'pass'
                  ? '当前门禁检查未发现阻断或待处理项，可以进入正式发布评审。'
                  : overall === 'pending'
                    ? '当前仍存在待处理项，尤其是修复后回归未执行或需人工复核，暂不应直接放行。'
                    : '当前仍存在阻断项，不建议直接进入正式发布。'
                : gateMode === 'missing_project'
                  ? '尚未选择客户项目，暂时无法形成发布结论。'
                  : '当前还没有生成门禁检查结果，需先完成一次真实扫描。'}
            </p>
          </div>
          <div className="release-summary-card">
            <strong>优先关注</strong>
            <p>
              {failCount > 0
                ? `当前有 ${failCount} 个阻塞检查项需要优先闭环。`
                : pendingCount > 0
                  ? `当前无显式阻断项，但仍有 ${pendingCount} 个待处理检查项需要确认。`
                  : '当前没有阻断项，可进入后续发布确认。'}
            </p>
          </div>
          <div className="release-summary-card">
            <strong>结果来源</strong>
            <p>所有门禁结论均基于当前项目的真实检测结果生成；修复后回归 Gate 来自 command center 的最新 regression_run / regression_suite_refresh，不混入样例或演示数据。</p>
          </div>
        </div>
      </section>

      {/* Check list */}
      <div className="check-list release-check-list">
        <div className="release-check-head">
          <span className="panel-kicker">检查明细</span>
          <h2>发布门禁项</h2>
        </div>
        {checks.length === 0 && !loading && (
          <div className="check-item">
            <span className="check-icon warning">!</span>
            <div className="check-copy flex-1">
              <strong>暂无门禁数据</strong>
              <p>运行一次完整扫描以生成发布门禁检查结果</p>
            </div>
          </div>
        )}
        {checks.map((c, index) => (
          <div key={`${c.name}-${index}`} className="check-item">
            <span className={`check-icon ${c.status}`}>
              {c.status === 'pass' ? '✓' : c.status === 'fail' ? '✗' : '!'}
            </span>
            <div className="check-copy flex-1">
              <strong>{c.name}</strong>
              <p>{c.detail}</p>
            </div>
            <span className={`status status-${c.status === 'pass' ? 'success' : c.status === 'fail' ? 'danger' : 'warning'}`}>
              {c.status.toUpperCase()}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default ReleaseGate;
