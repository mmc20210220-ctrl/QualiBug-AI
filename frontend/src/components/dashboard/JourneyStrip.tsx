interface JourneyStep {
  index: number;
  title: string;
  description: string;
  path: string;
  action: string;
}

interface JourneyStripProps {
  onNavigate: (path: string) => void;
}

const steps: JourneyStep[] = [
  { index: 1, title: '接入被测系统', description: '配置服务地址、测试账号与可选数据库校验', path: '/settings', action: '前往系统与环境' },
  { index: 2, title: '导入企业资料', description: '上传 PRD、接口规范等来源资料，系统据此建立项目上下文', path: '/materials', action: '导入资料' },
  { index: 3, title: '运行前检查并检测', description: '先核对真实运行条件，通过后再执行标准扫描；阻断状态不会被绕过', path: '/campaigns', action: '检查并运行' },
  { index: 4, title: '查看结果与发布建议', description: '先看总体结论、覆盖边界与下一步，再按需进入问题、证据和发布门禁', path: '/dashboard', action: '查看价值总览' },
];

/** 首次使用引导：接入 → 资料 → 运行前检查/检测 → 结果，全部指向真实页面。 */
export function JourneyStrip({ onNavigate }: JourneyStripProps) {
  return (
    <nav className="journey-strip" aria-label="使用流程">
      {steps.map((step) => (
        <button key={step.index} type="button" className="journey-step" onClick={() => onNavigate(step.path)}>
          <span className="journey-step-index" aria-hidden="true">{step.index}</span>
          <span className="journey-step-body">
            <strong>{step.title}</strong>
            <span>{step.description}</span>
            <em>{step.action} →</em>
          </span>
        </button>
      ))}
    </nav>
  );
}
