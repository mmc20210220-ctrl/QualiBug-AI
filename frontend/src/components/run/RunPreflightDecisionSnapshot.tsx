import type { RunPreflightPresentation } from '../../lib/run-preflight-presentation';
import './RunPreflightDecisionSnapshot.css';

type Props = {
  presentation: RunPreflightPresentation;
  running: boolean;
  runDisabled: boolean;
  onRun: () => void;
  onRefresh: () => void;
  onReview: () => void;
};

export function RunPreflightDecisionSnapshot({
  presentation,
  running,
  runDisabled,
  onRun,
  onRefresh,
  onReview,
}: Props) {
  const runPrimaryAction = () => {
    if (presentation.primaryAction === 'run') onRun();
    else if (presentation.primaryAction === 'refresh') onRefresh();
    else if (presentation.primaryAction === 'review') onReview();
  };

  const primaryDisabled = running
    || presentation.primaryAction === 'wait'
    || (presentation.primaryAction === 'run' && runDisabled);
  const primaryLabel = running && presentation.primaryAction === 'run'
    ? '正在自主验证…'
    : presentation.primaryActionLabel;

  return (
    <section
      className={`run-preflight-snapshot tone-${presentation.tone}`}
      aria-label="运行前检查结论"
      aria-live="polite"
    >
      <div className="run-preflight-head">
        <div>
          <span className="panel-kicker">运行前检查</span>
          <h2>{presentation.headline}</h2>
          <p>{presentation.summary}</p>
        </div>
        <span className={`run-preflight-authority tone-${presentation.tone}`}>
          {presentation.authorityLabel}
        </span>
      </div>

      <div className="run-preflight-facts" aria-label="运行辅助事实">
        {presentation.facts.map((fact) => (
          <article key={fact.label} className={`run-preflight-fact tone-${fact.tone}`}>
            <span>{fact.label}</span>
            <strong>{fact.value}</strong>
            <small>{fact.detail}</small>
          </article>
        ))}
      </div>

      <div className={`run-preflight-blocker tone-${presentation.tone}`}>
        <div>
          <span>当前运行结论</span>
          <strong>{presentation.blockerLabel}</strong>
          <small>{presentation.blockerDetail}</small>
        </div>
        {presentation.blockerCount > 1 && (
          <span className="summary-pill">共 {presentation.blockerCount} 项阻断</span>
        )}
      </div>

      <div className="run-preflight-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={runPrimaryAction}
          disabled={primaryDisabled}
          aria-describedby="run-preflight-authority-note"
        >
          {primaryLabel}
        </button>
        {presentation.primaryAction !== 'refresh' && presentation.primaryAction !== 'wait' && (
          <button type="button" className="btn btn-secondary" onClick={onRefresh} disabled={running}>
            重新检查
          </button>
        )}
      </div>

      <p className="run-preflight-authority-note" id="run-preflight-authority-note">
        只有后端 Preflight 的 <code>ready=true</code> 可以解释为“运行条件已通过”。目标系统、企业资料和凭据卡片只是辅助事实，不能单独放行扫描请求。
      </p>
    </section>
  );
}

export default RunPreflightDecisionSnapshot;
