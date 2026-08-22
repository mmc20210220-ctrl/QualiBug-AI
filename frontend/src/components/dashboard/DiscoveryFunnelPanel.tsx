import { useMemo, useState } from 'react';
import { asNum, asRecord, asText } from '../../lib/dashboard-utils';
import { GLOSSARY } from '../../lib/glossary';
import { TermHint } from '../TermHint';

type Props = {
  funnel: unknown;
  report?: unknown;
};

function listText(value: unknown, limit = 8): string[] {
  return Array.isArray(value)
    ? value.map(asText).filter(Boolean).slice(0, limit)
    : [];
}

export function DiscoveryFunnelPanel({ funnel, report }: Props) {
  const value = asRecord(funnel);
  const reportValue = asRecord(report);
  const health = asRecord(value.pipeline_health || reportValue.pipeline_health);
  const conservation = asRecord(value.conservation || health.funnel_conservation || reportValue.conservation);
  const reportMetrics = asRecord(reportValue.metrics);
  const details = (Array.isArray(value.top_blocking_reason_details)
    ? value.top_blocking_reason_details
    : Array.isArray(reportValue.unresolved_top_10)
      ? reportValue.unresolved_top_10
      : Array.isArray(reportValue.top_blocking_reasons)
        ? reportValue.top_blocking_reasons
        : [])
    .map(asRecord)
    .filter((row) => row.is_blocking !== false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const selected = details[selectedIndex] || details[0];
  const selectedExamples = selected
    ? (Array.isArray(selected.examples) ? selected.examples : []).map(asRecord)
    : [];
  const selectedExample = selectedExamples[0] || {};
  const selectedAttribution = selected ? asRecord(selected.loss_attribution) : {};
  const receiptCount = (field: string, reportField: string): number | string => {
    if (typeof conservation[field] === 'number' && Number.isFinite(conservation[field])) {
      return conservation[field] as number;
    }
    return typeof reportMetrics[reportField] === 'number' && Number.isFinite(reportMetrics[reportField])
      ? reportMetrics[reportField] as number
      : '未上报';
  };
  const selectedCount = receiptCount('selected_count', 'selected_count');
  const totalCount = receiptCount('generated_count', 'generated_count');
  const accountedCount = receiptCount('accounted_count', 'accounted_count');
  const terminalCount = receiptCount('terminal_count', 'terminal_count');
  const unaccountedCount = receiptCount('unaccounted_count', 'unaccounted_count');
  const deferredNotSelectedCount = receiptCount('not_selected_count', 'not_selected_count');
  const compileBlockedNotSelectedCount = receiptCount('compile_blocked_not_selected_count', 'compile_blocked_not_selected_count');
  const planBlockedCount = receiptCount('plan_blocked_count', 'plan_blocked_count');
  const compiledCount = receiptCount('compile_success_count', 'compiled_count');
  const executedCount = receiptCount('execution_count', 'executed_count');
  const executionBlockedCount = receiptCount('execution_blocked_count', 'execution_blocked_count');
  const compileBlockedCount = receiptCount('compile_blocked_count', 'compile_blocked_count');
  const compileDeferredCount = receiptCount('compile_deferred_count', 'compile_deferred_count');
  const oracleResolvedCount = receiptCount('oracle_resolved_count', 'oracle_resolved_count');
  const oracleViolationCount = receiptCount('oracle_violation_count', 'oracle_violation_count');
  const deliverableCount = receiptCount('customer_deliverable_finding_count', 'formal_delivery_count');
  const counts = useMemo(() => ([
    ['验证义务总数', totalCount],
    ['已有终态回执', accountedCount],
    ['缺少终态回执', unaccountedCount],
    ['递延未入选', deferredNotSelectedCount],
    ['编译阻断未入选', compileBlockedNotSelectedCount],
    ['计划阻断', planBlockedCount],
    ['已到终态', terminalCount],
    ['编译通过', compiledCount],
    ['编译阻断', compileBlockedCount],
    ['编译递延', compileDeferredCount],
    ['入选执行', selectedCount],
    ['真实执行', executedCount],
    ['执行阻断', executionBlockedCount],
    ['断言已判定', oracleResolvedCount],
    ['断言违规', oracleViolationCount],
    ['可交付问题', deliverableCount],
  ]), [accountedCount, compileBlockedCount, compileBlockedNotSelectedCount, compileDeferredCount, compiledCount, deferredNotSelectedCount, deliverableCount, executedCount, executionBlockedCount, oracleResolvedCount, oracleViolationCount, planBlockedCount, selectedCount, terminalCount, totalCount, unaccountedCount]);
  const healthStatus = asText(health.status) || 'UNKNOWN';
  const qualityStatus = asText(asRecord(reportValue.quality).status) || asText(asRecord(value.quality).status) || 'NOT_MEASURED';
  const reportStatus = asText(reportValue.report_status) || 'NOT_AVAILABLE';
  const conservationStatus = asText(conservation.status) || 'NOT_MEASURED';
  const identityStatus = asText(conservation.identity_status) || 'NOT_MEASURED';
  const reasonRegistry = asRecord(value.reason_registry || reportValue.reason_registry);
  const reasonRegistryStatus = asText(reasonRegistry.status) || 'NOT_MEASURED';
  const sourceFlow = asRecord(reportValue.source_flow || value.source_flow);
  const sourceMaterials = asRecord(sourceFlow.source_materials);
  const businessFacts = asRecord(sourceFlow.business_facts);
  const enterpriseBehaviorIr = asRecord(sourceFlow.enterprise_behavior_ir);
  const formalObligations = asRecord(sourceFlow.formal_obligations);
  const sourceFlowStatus = asText(sourceFlow.status) || 'NOT_MEASURED';
  const conversionRates = asRecord(reportValue.conversion_rates || value.conversion_rates);
  const conversionRows = (Array.isArray(conversionRates.rates) ? conversionRates.rates : [])
    .map(asRecord)
    .slice(0, 6);
  const displayCount = (raw: unknown): string => (
    typeof raw === 'number' && Number.isFinite(raw) ? String(raw) : '未上报'
  );
  const displayRate = (row: ReturnType<typeof asRecord>): string => (
    typeof row.rate === 'number' && Number.isFinite(row.rate)
      ? `${(row.rate * 100).toFixed(2)}%`
      : asText(row.status) || '未上报'
  );

  return (
    <section className="customer-secondary-card" aria-label="发现漏斗">
      <div className="focus-section-head">
        <div>
          <span className="customer-value-kicker"><TermHint label="发现漏斗" hint={GLOSSARY.discoveryFunnel} /></span>
          <h3>每条验证义务都有回执支撑的终态</h3>
        </div>
        <span className={`severity-badge ${healthStatus === 'OK' ? 'p2' : 'p0'}`}>
          {healthStatus}
        </span>
      </div>
      <p>
        以下计数来自义务尝试账本。外部质量结论保持「{qualityStatus}」；内部漏斗计数不等于召回率或精度。
      </p>
      <div className="customer-secondary-meta">
        <span><em>损失报告</em><b>{reportStatus}</b></span>
        <span><em>漏斗守恒</em><b>{conservationStatus}</b></span>
        <span><em>身份连续性</em><b>{identityStatus}</b></span>
        <span><em>原因登记表</em><b>{reasonRegistryStatus}</b></span>
      </div>
      <div className="customer-secondary-meta">
        {counts.map(([label, count]) => (
          <span key={label}><em>{label}</em><b>{count}</b></span>
        ))}
      </div>
      <div style={{ marginTop: 18 }}>
        <strong>资料到验证义务的证据流</strong>
        <div className="customer-secondary-meta">
          <span><em>流转状态</em><b>{sourceFlowStatus}</b></span>
          <span><em>企业资料</em><b>{displayCount(sourceMaterials.canonical_source_count)}</b></span>
          <span><em>业务事实</em><b>{displayCount(businessFacts.observed_row_count)}</b></span>
          <span><em>行为模型节点</em><b>{displayCount(enterpriseBehaviorIr.behavior_node_count)}</b></span>
          <span><em><TermHint label="验证义务" hint={GLOSSARY.verificationObligation} /></em><b>{displayCount(formalObligations.formal_obligation_count)}</b></span>
        </div>
        <div className="focus-list" style={{ marginTop: 10 }}>
          {conversionRows.length === 0 ? (
            <p>本轮没有记录转化率回执。</p>
          ) : conversionRows.map((row, index) => (
            <div className="focus-card" key={`${asText(row.name)}-${index}`}>
              <div className="focus-card-head">
                <strong>{asText(row.name) || '未命名转化'}</strong>
                <span>{displayRate(row)}</span>
              </div>
              <div className="focus-card-meta">
                <span>状态 <b>{asText(row.status) || '未上报'}</b></span>
                <span>分子 / 分母 <b>{displayCount(row.numerator_count)} / {displayCount(row.denominator_count)}</b></span>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div style={{ marginTop: 18 }}>
        <strong>主要阻断原因</strong>
        {details.length === 0 ? (
          <p>本轮没有记录阻断原因回执。</p>
        ) : (
          <div className="focus-list" style={{ marginTop: 10 }}>
            {details.slice(0, 5).map((row, index) => (
              <button
                type="button"
                key={`${asText(row.reason)}-${index}`}
                className="focus-card"
                style={{ textAlign: 'left', width: '100%', cursor: 'pointer' }}
                aria-pressed={selected === row}
                onClick={() => setSelectedIndex(index)}
              >
                <div className="focus-card-head">
                  <strong>{asText(row.reason) || '未登记原因'}</strong>
                  <span className="severity-badge p1">{asNum(row.count)}</span>
                </div>
                <div className="focus-card-meta">
                  <span>类别 <b>{asText(row.reason_family) || '未登记'}</b></span>
                  <span>阶段 <b>{asText(asRecord((Array.isArray(row.examples) ? row.examples[0] : undefined)).terminal_stage) || '未记录'}</b></span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
      {selected && (
        <div className="customer-secondary-card" style={{ marginTop: 16 }}>
          <span className="customer-value-kicker">所选阻断原因详情</span>
          <h4>{asText(selected.reason)}</h4>
          <div className="customer-secondary-meta">
            <span><em>业务 / 风险类别</em><b>{asText(selectedExample.risk_family) || '未记录'}</b></span>
            <span><em>阻断阶段</em><b>{asText(selectedExample.terminal_stage) || '未记录'}</b></span>
            <span><em>登记状态</em><b>{asText(selected.registry_status) || '未记录'}</b></span>
          </div>
          <p><strong>缺失内容：</strong>{listText(selected.customer_materials_needed, 4).join('; ') || '未记录'}</p>
          <p><strong>相关操作 / 接口：</strong>{listText(selectedExample.operation_refs).join(', ') || '未记录'}</p>
          <p><strong>身份范围：</strong>{listText(selectedExample.actor_refs).join(', ') || '未记录'}</p>
          <p><strong>资料证据：</strong>{Array.isArray(selectedExample.source_refs) && selectedExample.source_refs.length > 0 ? '已在来源回执中记录' : '未记录'}</p>
          <p><strong>损失归属：</strong>{asText(selectedAttribution.primary_owner) || '未知'}</p>
          <p><strong>资料充分性：</strong>{asText(selectedAttribution.source_evidence_sufficiency) || '未上报'}</p>
          <p><strong>回执详情：</strong>{asText(selectedExample.reason_detail) || '未记录'}</p>
        </div>
      )}
    </section>
  );
}

export default DiscoveryFunnelPanel;
