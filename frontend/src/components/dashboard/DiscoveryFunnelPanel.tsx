import { useMemo, useState } from 'react';
import { asNum, asRecord, asText, type JsonRecord } from '../../lib/dashboard-utils';

type Props = {
  funnel: unknown;
};

function stageValue(stages: JsonRecord[], name: string, field: string): number {
  const stage = stages.find((row) => asText(row.name) === name);
  return asNum(stage?.[field]);
}

function listText(value: unknown, limit = 8): string[] {
  return Array.isArray(value)
    ? value.map(asText).filter(Boolean).slice(0, limit)
    : [];
}

export function DiscoveryFunnelPanel({ funnel }: Props) {
  const value = asRecord(funnel);
  const stages = (Array.isArray(value.stages) ? value.stages : [])
    .map(asRecord);
  const health = asRecord(value.pipeline_health);
  const conservation = asRecord(value.conservation || health.funnel_conservation);
  const details = (Array.isArray(value.top_blocking_reason_details)
    ? value.top_blocking_reason_details
    : [])
    .map(asRecord)
    .filter((row) => row.is_blocking !== false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const selected = details[selectedIndex] || details[0];
  const selectedExamples = selected
    ? (Array.isArray(selected.examples) ? selected.examples : []).map(asRecord)
    : [];
  const selectedExample = selectedExamples[0] || {};
  const counts = useMemo(() => ([
    ['Obligations', asNum(conservation.selected_count, asNum(value.candidate_count))],
    ['Compiled', stageValue(stages, 'experiment_compile', 'success')],
    ['Executed', asNum(conservation.execution_count, stageValue(stages, 'governed_execution', 'success'))],
    ['Observed', stageValue(stages, 'observation', 'success')],
    ['Delivered', asNum(value.validated_bug_count)],
  ]), [conservation, stages, value]);
  const healthStatus = asText(health.status) || 'UNKNOWN';
  const qualityStatus = asText(asRecord(value.quality).status) || 'NOT_MEASURED';

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
        {counts.map(([label, count]) => (
          <span key={label}><em>{label}</em><b>{count}</b></span>
        ))}
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
          <p><strong>Receipt detail:</strong> {asText(selectedExample.reason_detail) || 'Not recorded'}</p>
        </div>
      )}
    </section>
  );
}

export default DiscoveryFunnelPanel;
