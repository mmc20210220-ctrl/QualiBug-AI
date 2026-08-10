import { deriveFindingDecisionPresentation } from '../../lib/finding-decision-presentation';
import { FindingVerificationStatus } from './FindingVerificationStatus';
import type { Finding } from '../../types';
import './FindingDecisionSnapshot.css';

type Props = {
  finding: Finding;
  compact?: boolean;
};

export function FindingDecisionSnapshot({ finding, compact = false }: Props) {
  const presentation = deriveFindingDecisionPresentation(finding);

  return (
    <section
      className={`finding-decision-snapshot${compact ? ' compact' : ''}`}
      aria-label="问题判断摘要"
    >
      <div className="finding-decision-head">
        <div>
          <span className="panel-kicker">问题判断摘要</span>
          <h3>先看结论，再看完整证据</h3>
        </div>
      </div>

      <div className="finding-decision-grid">
        <article className="finding-decision-cell finding-decision-impact">
          <span>发生了什么</span>
          <strong>{presentation.impact}</strong>
        </article>

        <article className="finding-decision-cell">
          <span>为什么成立</span>
          <strong>{presentation.basis}</strong>
        </article>

        <article className="finding-decision-cell">
          <span>证据状态</span>
          <strong>{presentation.evidenceLabel}</strong>
          <small>{presentation.evidenceDetail}</small>
        </article>

        <article className="finding-decision-cell">
          <span>当前验证结论</span>
          <FindingVerificationStatus finding={finding} compact />
          <small>最近真实验证：{presentation.latestVerificationAt}</small>
        </article>

        <article className="finding-decision-cell finding-decision-next">
          <span>下一步验证</span>
          <strong>{presentation.nextActionLabel}</strong>
          <small>{presentation.nextActionDetail}</small>
        </article>
      </div>

      <p className="finding-decision-boundary">
        这里仅汇总当前 Finding 已有的真实问题、证据与验证状态；不会根据前端展示自行判定“已修复”或替代项目级 Release Gate。
      </p>
    </section>
  );
}

export default FindingDecisionSnapshot;
