import { useSearchParams } from 'react-router-dom';
import { getCommercialAssets, usePipelineData, useReleaseData } from '../api/data';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import { TermHint } from '../components/TermHint';
import { GLOSSARY } from '../lib/glossary';
import { asRecord } from '../lib/value-guards';

type GateCheck = { name: string; status: 'pass' | 'fail' | 'pending'; detail: string };

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}
function bool(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') return ['true', '1', 'yes'].includes(value.trim().toLowerCase());
  return false;
}

function getCustomerDeliveryGuard(raw: unknown) {
  const record = asRecord(raw);
  const direct = asRecord(record.customer_delivery_guard);
  if (!Object.keys(direct).length) return null;
  return {
    status: text(direct.status),
    customer_deliverable: bool(direct.customer_deliverable),
    safe_for_customer: bool(direct.safe_for_customer),
    block_reasons: Array.isArray(direct.block_reasons) ? direct.block_reasons.map(text).filter(Boolean) : [],
    honesty_rule: text(direct.honesty_rule),
  };
}

export function ReleaseGate() {
  usePageTitle('发布门禁');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const { data: releaseData, loading } = useReleaseData(project);
  const { data: pipelineData } = usePipelineData(project);
  const commercialAssets = getCommercialAssets(pipelineData);
  const guard = getCustomerDeliveryGuard(pipelineData);

  const checks = (releaseData?.checks || []) as GateCheck[];
  const overall = releaseData?.overall || 'pass';
  const passCount = checks.filter(c => c.status === 'pass').length;
  const failCount = checks.filter(c => c.status === 'fail').length;
  const pendingCount = checks.filter(c => c.status === 'pending').length;
  const hasGateData = checks.length > 0;

  const lightColor: 'red' | 'yellow' | 'green' = !project || !hasGateData
    ? 'yellow'
    : overall === 'pass' ? 'green' : overall === 'pending' ? 'yellow' : 'red';

  const conclusion = !project
    ? '请选择项目后查看发布建议'
    : !hasGateData
      ? '运行一次完整扫描以生成发布门禁结果'
      : overall === 'pass'
        ? '建议发布：所有门禁检查已通过'
        : overall === 'pending'
          ? `谨慎发布：${pendingCount} 项待处理，需确认后放行`
          : `不建议发布：${failCount} 个阻断项未修复`;

  const deliveryLabel = guard
    ? (guard.customer_deliverable && guard.safe_for_customer ? '交付已放行' : '交付未放行')
    : commercialAssets?.commercial_handoff.safe_for_customer ? '交付已放行' : '交付未放行';

  return (
    <div>
      <section className="release-traffic-light">
        <div className={`traffic-light-orb ${lightColor}`} />
        <h1>{conclusion}</h1>
        <p>
          {hasGateData
            ? `${passCount}/${checks.length} 检查通过${failCount > 0 ? `，${failCount} 项阻塞` : ''}${pendingCount > 0 ? `，${pendingCount} 项待处理` : ''}`
            : '门禁结论基于真实检测结果自动生成'}
          {' · '}交付状态：{deliveryLabel}
        </p>
      </section>

      {loading && <div className="state-panel"><div className="spinner spinner-centered" /><p>评估发布就绪状态...</p></div>}

      <section className="release-checklist">
        <h2><TermHint label="发布门禁" hint={GLOSSARY.releaseGate} />检查清单</h2>
        {checks.length === 0 && !loading && (
          <div className="release-check-item">
            <span className="release-check-icon pending">!</span>
            <strong>暂无门禁数据</strong>
            <span className="check-detail">运行一次完整扫描以生成检查结果</span>
          </div>
        )}
        {checks.map((c, i) => (
          <div key={`${c.name}-${i}`} className="release-check-item">
            <span className={`release-check-icon ${c.status}`}>
              {c.status === 'pass' ? '✓' : c.status === 'fail' ? '✗' : '⏳'}
            </span>
            <strong>{c.name}</strong>
            <span className="check-detail">{c.detail}</span>
          </div>
        ))}
      </section>

      <section className="release-checklist">
        <h2><TermHint label="商业交付确认" hint="门禁通过只说明检查项通过；交付放行需要后端交付守卫（customer_delivery_guard）明确确认，两者独立判定。" /></h2>
        <div className="release-check-item">
          <span className={`release-check-icon ${deliveryLabel === '交付已放行' ? 'pass' : 'pending'}`}>
            {deliveryLabel === '交付已放行' ? '✓' : '!'}
          </span>
          <strong>{deliveryLabel}</strong>
          <span className="check-detail">
            {guard
              ? guard.customer_deliverable && guard.safe_for_customer
                ? 'customer_delivery_guard 已明确放行，可进入客户验收。'
                : guard.block_reasons.length > 0
                  ? `阻塞原因：${guard.block_reasons.join('、')}`
                  : guard.honesty_rule || '门禁通过不等于交付放行。'
              : '门禁通过本身不等于商业交付放行，需 Handoff 明确确认。'}
          </span>
        </div>
      </section>

      <div className="action-bar">
        <span className="action-bar-title">下一步</span>
        <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/findings', project)}>查看问题清单</button>
        <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据</button>
        <button className="btn btn-primary" onClick={() => navigateToProjectPath('/dashboard', project)}>返回价值总览</button>
      </div>
    </div>
  );
}

export default ReleaseGate;
