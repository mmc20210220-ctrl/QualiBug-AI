import { AnimatedCounter } from '../AnimatedCounter';
import { formatDurationMs } from '../../lib/display';

interface ScanFactsProps {
  testPoints: number;
  durationMs: number;
  modulesCount: number;
  evidenceTrust: number;
}

/**
 * 本轮检测事实：只展示来自后端真实执行与记账的数字。
 * 不做人工工时折算、金额估算或任何前端推算的营销数字。
 */
export function ScanFacts({ testPoints, durationMs, modulesCount, evidenceTrust }: ScanFactsProps) {
  return (
    <section className="roi-section">
      <div className="roi-section-head">
        <div>
          <h2>本轮检测事实</h2>
          <p>以下数字全部来自本轮真实执行与后端记账，不含任何估算</p>
        </div>
      </div>
      <div className="roi-cards">
        <div className="roi-card">
          <div className="roi-card-icon time">◎</div>
          <strong><AnimatedCounter value={testPoints} /></strong>
          <span className="roi-card-label">等效测试点</span>
          <span className="roi-card-hint">由本轮实际执行的验证探针折算</span>
        </div>
        <div className="roi-card">
          <div className="roi-card-icon coverage">▦</div>
          <strong><AnimatedCounter value={modulesCount} /></strong>
          <span className="roi-card-label">真实触达业务模块</span>
          <span className="roi-card-hint">仅统计本轮实际执行覆盖，非理论覆盖</span>
        </div>
        <div className="roi-card">
          <div className="roi-card-icon risk">⧗</div>
          <strong>{durationMs > 0 ? formatDurationMs(durationMs) : '未记录'}</strong>
          <span className="roi-card-label">本轮检测耗时</span>
          <span className="roi-card-hint">从发起检测到形成结论的真实耗时</span>
        </div>
      </div>
      <div className="roi-comparison">
        <div className="roi-comparison-item">
          <strong>{evidenceTrust > 0 ? `${evidenceTrust}%` : '待评估'}</strong>
          <span>结论可靠度（后端证据评分）</span>
        </div>
        <div className="roi-comparison-vs">=</div>
        <div className="roi-comparison-item muted">
          <strong>可验收</strong>
          <span>每个已确认问题都附原始请求 / 响应与复现路径</span>
        </div>
      </div>
    </section>
  );
}
