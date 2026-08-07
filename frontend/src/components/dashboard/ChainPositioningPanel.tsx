import { useMemo } from 'react';
import { asRecord, asText } from '../../lib/dashboard-utils';

type Props = {
  positioning?: unknown;
};

const STAGE_LABELS: Record<string, string> = {
  source_ingestion: '来源材料摄入',
  comprehension: '业务理解',
  hypothesis: '假设生成',
  obligation_compile: '义务编译',
  experiment_compile: '实验编译',
  execution: '执行',
  observation: '观察',
  verdict: '判定',
  delivery_gate: '交付门禁',
};

export function ChainPositioningPanel({ positioning }: Props) {
  const value = asRecord(positioning);
  const stages = Array.isArray(value.stages) ? value.stages.map(asRecord) : [];
  const firstLoss = asRecord(value.first_loss);
  const summary = asRecord(value.chain_summary);
  const topBlockers = Array.isArray(summary.top_blocker_codes)
    ? summary.top_blocker_codes.map(asRecord).slice(0, 5)
    : [];
  const firstLossStage = asText(firstLoss.stage) || 'NO_SIGNIFICANT_LOSS';
  const firstLossLabel = STAGE_LABELS[firstLossStage] || firstLossStage;
  const rows = useMemo(() => stages.map((stage) => {
    const codes = asRecord(stage.reason_code_breakdown);
    const codeText = Object.entries(codes)
      .slice(0, 3)
      .map(([code, count]) => `${code}×${count}`)
      .join(' ');
    const input = stage.input_count;
    const output = stage.output_count;
    const ratio = typeof input === 'number' && input > 0 && typeof output === 'number'
      ? Math.max(0, 1 - output / input)
      : null;
    return {
      label: STAGE_LABELS[asText(stage.stage)] || asText(stage.stage),
      input,
      output,
      blocked: stage.blocked_count ?? 0,
      ratio,
      codeText,
    };
  }), [stages]);

  if (stages.length === 0) {
    return (
      <section className="customer-secondary-card" aria-label="发现链路定位">
        <div className="focus-section-head">
          <div>
            <span className="customer-value-kicker">Chain positioning</span>
            <h3>发现链路卡点定位</h3>
          </div>
        </div>
        <p>
          暂无链路定位数据。运行一次新扫描后，这里会展示 9 个阶段的转化、阻塞原因码与修复建议。
        </p>
      </section>
    );
  }

  const display = (value: unknown): string => (
    typeof value === 'number' && Number.isFinite(value) ? String(value) : (asText(value) || '-')
  );

  return (
    <section className="customer-secondary-card" aria-label="发现链路定位">
      <div className="focus-section-head">
        <div>
          <span className="customer-value-kicker">Chain positioning</span>
          <h3>卡在哪：链路定位</h3>
        </div>
        <span className={`severity-badge ${firstLossStage === 'NO_SIGNIFICANT_LOSS' ? 'p2' : 'p0'}`}>
          {firstLossLabel}
        </span>
      </div>
      <p>
        第一损失点 <b>{firstLossLabel}</b>（{asText(firstLoss.basis) || '无显著损失'}）。
        下表为诊断定位信息，原因码含义与修复建议为合成诊断文本，不构成交付证据。
      </p>
      <div className="customer-secondary-meta">
        {rows.map((row) => (
          <span key={row.label} title={row.codeText || undefined}>
            <em>{row.label}</em>
            <b>
              {display(row.input)}→{display(row.output)}
              {typeof row.blocked === 'number' && row.blocked > 0 ? ` 阻塞${row.blocked}` : ''}
              {row.ratio !== null ? ` ${(row.ratio * 100).toFixed(0)}%` : ''}
            </b>
          </span>
        ))}
      </div>
      {topBlockers.length > 0 && (
        <div style={{ marginTop: 14 }}>
          {topBlockers.map((blocker) => (
            <div key={asText(blocker.reason_code)} style={{ marginBottom: 10 }}>
              <strong>{asText(blocker.reason_code)} ×{display(blocker.count)}</strong>
              <div style={{ opacity: 0.85 }}>{asText(blocker.meaning)}</div>
              <div style={{ opacity: 0.7, fontSize: 13 }}>
                建议：{asText(blocker.suggested_action)}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
