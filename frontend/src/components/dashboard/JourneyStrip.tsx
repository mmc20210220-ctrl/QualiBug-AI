import { useOnboardingProgress } from '../../lib/onboarding-progress';

interface JourneyStripProps {
  project?: string;
  onNavigate: (path: string) => void;
}

/**
 * 全局进度主线（唯一渲染方）：步骤定义与完成判定来自
 * lib/onboarding-progress 的单一口径，本组件只负责展示 ✓ / 当前步 / 待完成。
 */
export function JourneyStrip({ project = '', onNavigate }: JourneyStripProps) {
  const progress = useOnboardingProgress(project);

  return (
    <nav className="journey-strip" aria-label="使用流程进度">
      {!project && (
        <p className="journey-summary">
          选择客户后，这里会实时显示接入进度；完成判定全部来自后端真实状态。
        </p>
      )}
      {project && (
        <p className="journey-summary" role="status">
          {progress.loading
            ? '正在核对真实接入状态…'
            : `已完成 ${progress.completedCount}/${progress.total} 步${progress.currentStep ? ` · 当前：${progress.currentStep.title}` : ' · 主线已走完，可在运行中心继续检测'}`}
          {progress.warning && <span className="settings-inline-feedback" role="alert"> {progress.warning}</span>}
        </p>
      )}
      {progress.steps.map((step) => {
        const isCurrent = !step.done && progress.currentStep?.key === step.key;
        const stateClass = step.done ? ' done' : isCurrent ? ' current' : '';
        return (
          <button
            key={step.key}
            type="button"
            className={`journey-step${stateClass}`}
            aria-current={isCurrent ? 'step' : undefined}
            onClick={() => onNavigate(step.path)}
          >
            <span className="journey-step-index" aria-hidden="true">{step.done ? '✓' : step.index}</span>
            <span className="journey-step-body">
              <strong>{step.title}</strong>
              <span>{step.description}</span>
              <em>
                {step.value}
                {' · '}
                {isCurrent ? `${step.actionLabel} →` : step.done ? '已完成' : '待完成'}
              </em>
            </span>
          </button>
        );
      })}
    </nav>
  );
}
