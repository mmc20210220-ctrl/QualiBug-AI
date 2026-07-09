import { useSearchParams } from 'react-router-dom';
import { getCommercialAssets, usePipelineData, useReleaseData } from '../api/data';
import { usePageTitle } from '../lib/page-title';

type GateCheck = { name: string; status: 'pass' | 'fail' | 'pending'; detail: string };
type HandoffTone = 'pass' | 'fail' | 'pending';
type JsonRecord = Record<string, unknown>;
type CustomerDeliveryGuard = {
  status: string;
  customer_deliverable: boolean;
  safe_for_customer: boolean;
  release_gate_overall_status: string;
  tracker_payload_status: string;
  delivery_package_release_verdict: string;
  block_reasons: string[];
  honesty_rule: string;
};

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function bool(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (['true', '1', 'yes', 'y', 'on'].includes(normalized)) return true;
  }
  return false;
}

function getCustomerDeliveryGuard(raw: unknown): CustomerDeliveryGuard | null {
  const record = asRecord(raw);
  const direct = asRecord(record.customer_delivery_guard);
  if (!Object.keys(direct).length) return null;
  const blockReasons = Array.isArray(direct.block_reasons) ? direct.block_reasons.map(text).filter(Boolean) : [];
  return {
    status: text(direct.status),
    customer_deliverable: bool(direct.customer_deliverable),
    safe_for_customer: bool(direct.safe_for_customer),
    release_gate_overall_status: text(direct.release_gate_overall_status),
    tracker_payload_status: text(direct.tracker_payload_status),
    delivery_package_release_verdict: text(direct.delivery_package_release_verdict),
    block_reasons: blockReasons,
    honesty_rule: text(direct.honesty_rule),
  };
}

function guardLabel(guard: CustomerDeliveryGuard | null): string {
  if (!guard) return '';
  if (guard.customer_deliverable && guard.safe_for_customer) return '交付已放行';
  if (guard.status === 'blocked_by_release_gate' || guard.release_gate_overall_status === 'fail') return '交付被门禁阻塞';
  if (guard.status === 'hold_for_validation' || guard.release_gate_overall_status === 'pending') return '交付待复核';
  if (guard.status === 'hold_for_commercial_handoff') return '等待 Handoff 放行';
  if (guard.status === 'blocked_missing_release_gate') return '缺少发布门禁';
  return guard.status || '交付未放行';
}

function handoffLabel(commercialAssets: ReturnType<typeof getCommercialAssets>, guard: CustomerDeliveryGuard | null): string {
  const fromGuard = guardLabel(guard);
  if (fromGuard) return fromGuard;
  if (!commercialAssets) return '暂无 Handoff';
  const safeForCustomer = commercialAssets.commercial_handoff.safe_for_customer;
  const acceptance = text(commercialAssets.commercial_handoff.acceptance_status);
  const tracker = text(commercialAssets.tracker_sync.payload_status);
  const packageVerdict = text(commercialAssets.delivery_package.release_verdict);
  const releaseGate = text(commercialAssets.release_gate_overall_status || commercialAssets.commercial_handoff.release_gate_status || commercialAssets.delivery_package.release_gate_overall_status);
  if (safeForCustomer) return '交付已放行';
  if (acceptance === 'blocked_by_release_gate' || tracker === 'blocked_by_release_gate' || releaseGate === 'fail' || packageVerdict === 'fail') return '交付被门禁阻塞';
  if (acceptance === 'hold_for_validation' || tracker === 'hold_for_validation' || releaseGate === 'pending' || packageVerdict === 'pending') return '交付待复核';
  return '交付未放行';
}

function handoffTone(commercialAssets: ReturnType<typeof getCommercialAssets>, guard: CustomerDeliveryGuard | null): HandoffTone {
  const label = handoffLabel(commercialAssets, guard);
  if (label === '交付已放行') return 'pass';
  if (label === '交付被门禁阻塞' || label === '缺少发布门禁') return 'fail';
  return 'pending';
}

function handoffMessage(commercialAssets: ReturnType<typeof getCommercialAssets>, guard: CustomerDeliveryGuard | null, gateTitle: string): string {
  if (guard) {
    if (guard.customer_deliverable && guard.safe_for_customer) return 'customer_delivery_guard 已明确 customer_deliverable=true 且 safe_for_customer=true，可以进入客户验收流程。';
    const reasons = guard.block_reasons.length ? `阻塞原因：${guard.block_reasons.join('、')}。` : '';
    return `${reasons}${guard.honesty_rule || 'customer_delivery_guard 是交付状态的机器可读真相源；门禁通过本身不等于客户交付放行。'}`;
  }
  if (!commercialAssets) return '当前 command center 未返回 customer_delivery_guard 或 commercial_assets。发布门禁结论不能自动等同于商业交付放行。';
  const reason = text(commercialAssets.delivery_package.release_gate_block_reason || commercialAssets.release_gate_honesty_rule);
  if (commercialAssets.commercial_handoff.safe_for_customer) return '后端已明确标记 commercial_handoff.safe_for_customer=true，可以进入客户验收流程。';
  if (handoffTone(commercialAssets, guard) === 'fail') return reason || '当前存在失败门禁项，交付包不得声明为客户可验收状态。';
  if (handoffTone(commercialAssets, guard) === 'pending') return reason || '当前仍需回归执行或人工复核；只有 Handoff 明确放行后才可交付。';
  return `当前发布门禁为「${gateTitle}」，但商业交付 Handoff 尚未明确放行。`;
}

export function ReleaseGate() {
  usePageTitle('发布门禁');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { data: releaseData, loading } = useReleaseData(project);
  const { data: pipelineData } = usePipelineData(project);
  const commercialAssets = getCommercialAssets(pipelineData);
  const customerDeliveryGuard = getCustomerDeliveryGuard(pipelineData);

  const checks = (releaseData?.checks || []) as GateCheck[];
  const overall = releaseData?.overall || 'pass';
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
  const commercialLabel = handoffLabel(commercialAssets, customerDeliveryGuard);
  const commercialTone = handoffTone(commercialAssets, customerDeliveryGuard);
  const safeForCustomerText = customerDeliveryGuard ? String(customerDeliveryGuard.safe_for_customer) : commercialAssets ? String(commercialAssets.commercial_handoff.safe_for_customer) : '未上报';
  const trackerStatus = customerDeliveryGuard?.tracker_payload_status || commercialAssets?.tracker_sync.payload_status || '未上报';
  const packageVerdict = customerDeliveryGuard?.delivery_package_release_verdict || commercialAssets?.delivery_package.release_verdict || commercialAssets?.delivery_package.status || '未上报';
  const summaryCards = [
    { label: '通过', value: gateMode === 'data' ? passCount : '--', tone: 'success' },
    { label: '阻塞', value: gateMode === 'data' ? failCount : '--', tone: 'danger' },
    { label: '待处理', value: gateMode === 'data' ? pendingCount : '--', tone: 'warning' },
    { label: '交付 Handoff', value: commercialLabel, tone: commercialTone === 'pass' ? 'success' : commercialTone === 'fail' ? 'danger' : 'warning' },
  ];

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">发布评审</span>
          <h1>发布门禁</h1>
          <p>把检测结果沉淀为可执行的发布结论，同时把“发布门禁”和“商业交付 Handoff”分开展示；如后端返回 customer_delivery_guard，本页以它作为交付真相源。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">当前结论 {gateTitle}</span>
            <span className="summary-pill">通过 {gateMode === 'data' ? passCount : '--'}</span>
            <span className="summary-pill">阻塞 {gateMode === 'data' ? failCount : '--'}</span>
            <span className="summary-pill">待处理 {gateMode === 'data' ? pendingCount : '--'}</span>
            <span className="summary-pill">交付 Handoff {commercialLabel}</span>
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
                  ? '当前门禁检查未发现阻断或待处理项，可以进入正式发布评审；但仍需查看 customer_delivery_guard / 商业交付 Handoff 是否明确放行。'
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
            <p>门禁结论基于当前项目真实检测结果；交付状态优先读取 command center 顶层 customer_delivery_guard，缺失时才降级读取 commercial_assets。</p>
          </div>
        </div>
      </section>

      <section className="release-summary-panel mb-4">
        <div className="release-summary-head">
          <div>
            <span className="panel-kicker">商业交付 Handoff</span>
            <h2>门禁通过不等于整包交付</h2>
          </div>
        </div>
        <div className="release-summary-grid">
          <div className="release-summary-card">
            <strong>交付安全状态</strong>
            <p>{commercialLabel}</p>
          </div>
          <div className="release-summary-card">
            <strong>Handoff 说明</strong>
            <p>{handoffMessage(commercialAssets, customerDeliveryGuard, gateTitle)}</p>
          </div>
          <div className="release-summary-card">
            <strong>交付口径</strong>
            <p>
              guard：{customerDeliveryGuard?.status || '未上报'}；
              customer_deliverable：{customerDeliveryGuard ? String(customerDeliveryGuard.customer_deliverable) : '未上报'}；
              safe_for_customer：{safeForCustomerText}；
              tracker：{trackerStatus}；
              package：{packageVerdict}。
            </p>
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
