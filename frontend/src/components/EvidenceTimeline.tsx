import type { EvidenceStep } from '../types';
import { formatActorName, formatDurationMs, formatResponseSummary } from '../lib/display';

interface EvidenceTimelineProps {
  steps: EvidenceStep[];
}

const TAG_META: Record<string, { label: string; className: string }> = {
  rule: { label: '规则', className: 'tag-rule' },
  api: { label: '请求', className: 'tag-api' },
  fact: { label: '事实', className: 'tag-fact' },
  response: { label: '响应', className: 'tag-response' },
  db: { label: '数据', className: 'tag-db' },
  log: { label: '日志', className: 'tag-log' },
  judgment: { label: '判定', className: 'tag-judgment' },
};

const CONFIDENCE_LABEL: Record<string, string> = {
  high: '高可信',
  medium: '中可信',
  low: '低可信',
};

const SOURCE_LABEL: Record<string, string> = {
  document: '文档',
  har: 'HAR',
  db: '数据库',
  log: '日志',
  engine: '引擎',
  replay: '复现',
};

function StatusCodeBadge({ code }: { code: number }) {
  if (!code) return null;
  let tone = 'status-ok';
  if (code >= 500) tone = 'status-error';
  else if (code >= 400) tone = 'status-warn';
  else if (code >= 300) tone = 'status-redirect';
  return <span className={`status-code-badge ${tone}`}>{code}</span>;
}

function StructuredBlock({ step }: { step: EvidenceStep }) {
  const s = step.structured;
  if (!s) return null;

  if (step.tag === 'response') {
    const body = String(s.response_body || '').slice(0, 500);
    const durationMs = Number(s.duration_ms) || 0;
    const actor = String(s.actor || '').trim();
    return (
      <div className="evidence-structured">
        <div className="evidence-structured-row">
          <StatusCodeBadge code={Number(s.status_code) || 0} />
          {durationMs > 0 && <span className="evidence-meta-chip">耗时 {formatDurationMs(durationMs)}</span>}
          {actor && <span className="evidence-meta-chip">操作者: {formatActorName(actor)}</span>}
        </div>
        {body && <code className="evidence-code-block">{body}</code>}
      </div>
    );
  }

  if (step.tag === 'db') {
    const table = String(s.table || '').trim();
    const column = String(s.column || '').trim();
    const value = s.value === undefined || s.value === '' ? '' : String(s.value);
    const businessKey = String(s.business_key || '').trim();
    const violation = String(s.violation || '').trim();
    return (
      <div className="evidence-structured">
        <div className="evidence-db-table">
          {table && <span className="evidence-db-cell"><em>表</em><code>{table}</code></span>}
          {column && <span className="evidence-db-cell"><em>字段</em><code>{column}</code></span>}
          {value && <span className="evidence-db-cell"><em>当前值</em><code className="evidence-db-value">{value}</code></span>}
          {businessKey && <span className="evidence-db-cell"><em>业务主键</em><code>{businessKey}</code></span>}
        </div>
        {violation && <p className="evidence-db-violation">{violation}</p>}
      </div>
    );
  }

  const traceId = String(s.trace_id || '').trim();
  if (step.tag === 'log' && traceId) {
    return (
      <div className="evidence-structured">
        <code className="evidence-trace-id-inline">{traceId}</code>
      </div>
    );
  }

  return null;
}

function getStepContent(step: EvidenceStep) {
  if (step.tag === 'response') {
    return formatResponseSummary(step.content || '', step.structured);
  }

  return step.content || '暂无内容';
}

export function EvidenceTimeline({ steps }: EvidenceTimelineProps) {
  if (!steps || steps.length === 0) {
    return <div className="evidence-timeline-empty">暂无证据链数据</div>;
  }

  return (
    <div className="evidence-timeline">
      {steps.map((step, idx) => {
        const tagMeta = TAG_META[step.tag] || TAG_META.fact;
        return (
          <div key={idx} className="evidence-timeline-node">
            <div className="evidence-timeline-marker">
              <span className={`evidence-timeline-dot ${tagMeta.className}`}>{idx + 1}</span>
              {idx < steps.length - 1 && <div className="evidence-timeline-connector" />}
            </div>
            <div className="evidence-timeline-content">
              <div className="evidence-timeline-head">
                <span className={`evidence-timeline-tag ${tagMeta.className}`}>{tagMeta.label}</span>
                <strong>{step.label}</strong>
                <span className="evidence-timeline-badges">
                  {step.source && <span className="evidence-source-chip-sm">{SOURCE_LABEL[step.source] || step.source}</span>}
                  {step.confidence && <span className={`evidence-confidence-chip ${step.confidence}`}>{CONFIDENCE_LABEL[step.confidence] || step.confidence}</span>}
                </span>
              </div>
              <p className="evidence-timeline-text">{getStepContent(step)}</p>
              <StructuredBlock step={step} />
              {step.detail && <em className="evidence-timeline-detail">{step.detail}</em>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default EvidenceTimeline;
