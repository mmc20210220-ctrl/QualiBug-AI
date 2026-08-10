import type { ReleasePresentation } from '../../lib/release-presentation';
import './ReleaseDecisionSnapshot.css';

type FactTone = 'success' | 'warning' | 'danger' | 'neutral';

type DecisionFact = {
  label: string;
  value: string;
  detail: string;
  tone: FactTone;
};

type Props = {
  presentation: ReleasePresentation;
  conclusion: string;
  gateFact: DecisionFact;
  regressionFact: DecisionFact;
  nextAction: { label: string; detail: string };
  onNextAction: () => void;
};

export function ReleaseDecisionSnapshot({
  presentation,
  conclusion,
  gateFact,
  regressionFact,
  nextAction,
  onNextAction,
}: Props) {
  return (
    <section className={`release-decision-snapshot tone-${presentation.color}`} aria-label="当前发布决策摘要">
      <div className="release-decision-head">
        <div>
          <span className="panel-kicker">项目级发布结论</span>
          <h1>{conclusion}</h1>
          <p>{presentation.advice}</p>
        </div>
        <div className={`release-decision-verdict tone-${presentation.color}`}>
          <span className="release-decision-orb" aria-hidden="true" />
          <strong>{presentation.label}</strong>
        </div>
      </div>

      <div className="release-decision-facts">
        {[gateFact, regressionFact].map((fact) => (
          <article key={fact.label} className={`release-decision-fact tone-${fact.tone}`}>
            <span>{fact.label}</span>
            <strong>{fact.value}</strong>
            <small>{fact.detail}</small>
          </article>
        ))}
      </div>

      <article className="release-decision-next">
        <div>
          <span>现在最应该做</span>
          <strong>{nextAction.label}</strong>
          <small>{nextAction.detail}</small>
        </div>
        <button type="button" className="btn btn-primary" onClick={onNextAction}>
          {nextAction.label}
        </button>
      </article>

      <p className="release-decision-boundary">
        这里不根据“0 个问题”或前端自算检查项放行发布。绿色只来自共享 Release Presentation 对真实项目级 Gate 的明确通过结论；单条 Finding 与单次回归都不能独立覆盖项目级发布门禁。
      </p>
    </section>
  );
}

export default ReleaseDecisionSnapshot;
