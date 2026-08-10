import type { Finding } from '../../types';
import { AnimatedCounter } from '../AnimatedCounter';
import { FindingVerificationStatus } from '../findings/FindingVerificationStatus';
import { RiskRing, ReleaseLight } from './DashboardPrimitives';
import './ValueHero.css';

interface ValueHeroProps {
  projectName: string;
  conclusion: string;
  headline: string;
  level: 'safe' | 'attention' | 'blocked';
  decision: { color: 'red' | 'yellow' | 'green'; label: string; advice: string };
  metrics: {
    confirmedDefects: number;
    p0Count: number;
    p1Count: number;
    evidencePackCount: number;
  };
  scanTime: string;
  focusFinding: Finding | null;
  nextAction: { title: string; label: string };
  onNextAction: () => void;
  onOpenFocus: () => void;
}

export function ValueHero({
  projectName,
  conclusion,
  headline,
  level,
  decision,
  metrics,
  scanTime,
  focusFinding,
  nextAction,
  onNextAction,
  onOpenFocus,
}: ValueHeroProps) {
  return (
    <section className="value-hero dashboard-decision-hero" aria-label="本次验证决策摘要">
      <span className="value-hero-eyebrow">{projectName} · 本次验证结论</span>

      <div className="value-hero-conclusion">
        <RiskRing level={level} />
        <div>
          <h1>{conclusion}</h1>
          <p>{headline}</p>
        </div>
      </div>

      <div className="value-hero-decision dashboard-decision-release">
        <ReleaseLight color={decision.color} label={decision.label} advice={decision.advice} />
        <div className="dashboard-decision-scan-time">最近检测 {scanTime}</div>
      </div>

      <div className="value-hero-metrics dashboard-decision-metrics" aria-label="真实问题规模">
        <div className="value-hero-metric">
          <AnimatedCounter value={metrics.confirmedDefects} className="" />
          <span>已确认问题</span>
        </div>
        <div className="value-hero-metric">
          <strong className={metrics.p0Count > 0 ? 'danger' : ''}>
            <AnimatedCounter value={metrics.p0Count} className="" />
          </strong>
          <span>已确认 P0</span>
        </div>
        <div className="value-hero-metric">
          <AnimatedCounter value={metrics.p1Count} className="" />
          <span>已确认 P1</span>
        </div>
        <div className="value-hero-metric">
          <AnimatedCounter value={metrics.evidencePackCount} className="" />
          <span>真实证据包</span>
        </div>
      </div>

      <div className="dashboard-decision-bottom">
        <article className="dashboard-decision-next">
          <span>现在最应该做</span>
          <strong>{nextAction.title}</strong>
          <button type="button" className="btn btn-primary" onClick={onNextAction}>
            {nextAction.label}
          </button>
        </article>

        <article className="dashboard-decision-focus">
          <span>最高优先问题</span>
          {focusFinding ? (
            <>
              <div className="dashboard-decision-focus-title">
                <span className={`severity-badge ${focusFinding.severity.toLowerCase()}`}>{focusFinding.severity}</span>
                <strong>{focusFinding.title}</strong>
              </div>
              <p>{focusFinding.business_summary || focusFinding.business_impact?.summary || focusFinding.actual || '该问题已形成确认结论。'}</p>
              <FindingVerificationStatus finding={focusFinding} compact />
              <button type="button" className="btn btn-secondary btn-sm" onClick={onOpenFocus}>
                查看这条验证
              </button>
            </>
          ) : (
            <p>当前没有已确认问题可作为最高优先项；是否可发布仍以项目级发布门禁结论为准。</p>
          )}
        </article>
      </div>

      <p className="dashboard-decision-boundary">
        这里不根据“0 个问题”、测试点数量或前端评分自行推导安全；发布判断继续服从项目级 Release Gate、回归状态和真实覆盖边界。
      </p>
    </section>
  );
}
