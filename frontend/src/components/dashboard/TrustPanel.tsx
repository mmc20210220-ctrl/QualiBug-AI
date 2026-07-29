export type TrustTone = 'success' | 'warning' | 'danger' | 'neutral';

export interface TrustSignal {
  key: string;
  title: string;
  tone: TrustTone;
  statusLabel: string;
  description: string;
  /** true 表示该信号的后端数据尚未提供，展示为诚实待接入态 */
  unreported?: boolean;
}

const toneIcon: Record<TrustTone, string> = {
  success: '✓',
  warning: '!',
  danger: '✕',
  neutral: '·',
};

interface TrustPanelProps {
  signals: TrustSignal[];
}

/**
 * 「为什么可信」面板：把治理、健康、交付守卫、清理回执、证据可靠度
 * 等信任信号并排展示。每个信号 = 图标 + 颜色 + 文字（不依赖颜色单独传达）。
 * 后端未提供的数据如实标注「后端暂未提供」，绝不虚构。
 */
export function TrustPanel({ signals }: TrustPanelProps) {
  if (!signals.length) return null;
  return (
    <section className="trust-section">
      <div className="trust-section-head">
        <h2>为什么可信</h2>
        <p>结论建立在真实执行、受控写入与可追溯回执之上；未上报的信号如实标注</p>
      </div>
      <div className="trust-grid">
        {signals.map((signal) => (
          <article key={signal.key} className={`trust-card tone-${signal.tone}${signal.unreported ? ' unreported' : ''}`}>
            <div className="trust-card-head">
              <span className={`trust-status-chip tone-${signal.tone}`} aria-hidden="true">{toneIcon[signal.tone]}</span>
              <strong>{signal.title}</strong>
              {signal.unreported && <span className="trust-unreported-badge">后端暂未提供</span>}
            </div>
            <span className="trust-card-status">{signal.statusLabel}</span>
            <p>{signal.description}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
