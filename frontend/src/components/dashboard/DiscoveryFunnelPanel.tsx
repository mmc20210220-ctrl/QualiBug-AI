import { useMemo, useState } from 'react';
import { asNum, asRecord, asText } from '../../lib/dashboard-utils';

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
      : 'NOT_MEASURED';
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
    ['Formal obligations', totalCount],
    ['Accounted terminal outcomes', accountedCount],
    ['Unaccounted obligations', unaccountedCount],
    ['Deferred, not selected', deferredNotSelectedCount],
    ['Compile blocked, not selected', compileBlockedNotSelectedCount],
    ['Plan blocked', planBlockedCount],
    ['Terminal', terminalCount],
    ['Compiled', compiledCount],
    ['Compile blocked', compileBlockedCount],
    ['Compile deferred', compileDeferredCount],
    ['Selected', selectedCount],
    ['Executed', executedCount],
    ['Execution blocked', executionBlockedCount],
    ['Oracle resolved', oracleResolvedCount],
    ['Oracle violations', oracleViolationCount],
    ['Deliverable findings', deliverableCount],
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
    typeof raw === 'number' && Number.isFinite(raw) ? String(raw) : 'NOT_MEASURED'
  );
  const displayRate = (row: ReturnType<typeof asRecord>): string => (
    typeof row.rate === 'number' && Number.isFinite(row.rate)
      ? `${(row.rate * 100).toFixed(2)}%`
      : asText(row.status) || 'NOT_MEASURED'
  );

  return (
    <section className="customer-secondary-card" aria-label="Discovery funnel">
      <div className="focus-section-head">
        <div>
          <span className="customer-value-kicker">Discovery funnel</span>
          <h3>Every obligation has a receipt-backed outcome</h3>
        </div>
        <span className={`severity-badge ${healthStatus === 'OK' ? 'p2' : 'p0'}`}>
          {healthStatus}
        </span>
      </div>
      <p>
        Counts below come from the obligation-attempt ledger. External quality remains {qualityStatus}; internal funnel counts are not recall or precision.
      </p>
      <div className="customer-secondary-meta">
        <span><em>Loss report</em><b>{reportStatus}</b></span>
        <span><em>Funnel conservation</em><b>{conservationStatus}</b></span>
        <span><em>Identity continuity</em><b>{identityStatus}</b></span>
        <span><em>Reason registry</em><b>{reasonRegistryStatus}</b></span>
      </div>
      <div className="customer-secondary-meta">
        {counts.map(([label, count]) => (
          <span key={label}><em>{label}</em><b>{count}</b></span>
        ))}
      </div>
      <div style={{ marginTop: 18 }}>
        <strong>Source-to-obligation evidence</strong>
        <div className="customer-secondary-meta">
          <span><em>Flow status</em><b>{sourceFlowStatus}</b></span>
          <span><em>Source materials</em><b>{displayCount(sourceMaterials.canonical_source_count)}</b></span>
          <span><em>Business facts</em><b>{displayCount(businessFacts.observed_row_count)}</b></span>
          <span><em>Enterprise Behavior IR</em><b>{displayCount(enterpriseBehaviorIr.behavior_node_count)}</b></span>
          <span><em>Formal obligations</em><b>{displayCount(formalObligations.formal_obligation_count)}</b></span>
        </div>
        <div className="focus-list" style={{ marginTop: 10 }}>
          {conversionRows.length === 0 ? (
            <p>No conversion-rate receipt was recorded.</p>
          ) : conversionRows.map((row, index) => (
            <div className="focus-card" key={`${asText(row.name)}-${index}`}>
              <div className="focus-card-head">
                <strong>{asText(row.name) || 'UNNAMED_CONVERSION'}</strong>
                <span>{displayRate(row)}</span>
              </div>
              <div className="focus-card-meta">
                <span>Status <b>{asText(row.status) || 'NOT_MEASURED'}</b></span>
                <span>Numerator / denominator <b>{displayCount(row.numerator_count)} / {displayCount(row.denominator_count)}</b></span>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div style={{ marginTop: 18 }}>
        <strong>Top blocking reasons</strong>
        {details.length === 0 ? (
          <p>No blocking reason receipt was recorded.</p>
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
                  <strong>{asText(row.reason) || 'UNREGISTERED_REASON'}</strong>
                  <span className="severity-badge p1">{asNum(row.count)}</span>
                </div>
                <div className="focus-card-meta">
                  <span>Family <b>{asText(row.reason_family) || 'UNREGISTERED'}</b></span>
                  <span>Stage <b>{asText(asRecord((Array.isArray(row.examples) ? row.examples[0] : undefined)).terminal_stage) || 'Not recorded'}</b></span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
      {selected && (
        <div className="customer-secondary-card" style={{ marginTop: 16 }}>
          <span className="customer-value-kicker">Selected blocker detail</span>
          <h4>{asText(selected.reason)}</h4>
          <div className="customer-secondary-meta">
            <span><em>Business/risk family</em><b>{asText(selectedExample.risk_family) || 'Not recorded'}</b></span>
            <span><em>Blocking stage</em><b>{asText(selectedExample.terminal_stage) || 'Not recorded'}</b></span>
            <span><em>Registry</em><b>{asText(selected.registry_status) || 'Not recorded'}</b></span>
          </div>
          <p><strong>Missing content:</strong> {listText(selected.customer_materials_needed, 4).join('; ') || 'Not recorded'}</p>
          <p><strong>Related operation/API:</strong> {listText(selectedExample.operation_refs).join(', ') || 'Not recorded'}</p>
          <p><strong>Actor scope:</strong> {listText(selectedExample.actor_refs).join(', ') || 'Not recorded'}</p>
          <p><strong>Source evidence:</strong> {Array.isArray(selectedExample.source_refs) && selectedExample.source_refs.length > 0 ? 'Available in the source receipt' : 'Not recorded'}</p>
          <p><strong>Loss owner:</strong> {asText(selectedAttribution.primary_owner) || 'UNKNOWN'}</p>
          <p><strong>Source sufficiency:</strong> {asText(selectedAttribution.source_evidence_sufficiency) || 'NOT_MEASURED'}</p>
          <p><strong>Receipt detail:</strong> {asText(selectedExample.reason_detail) || 'Not recorded'}</p>
        </div>
      )}
    </section>
  );
}

export default DiscoveryFunnelPanel;
