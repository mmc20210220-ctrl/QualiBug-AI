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
  { index: 1, title: '接入被测系统', description: '配置服务地址、测试账号与可选数据库校验', path: '/settings', action: '前往项目设置' },
  { index: 2, title: '导入企业资料', description: '上传 PRD、接口规范等来源资料，系统据此建模业务行为', path: '/materials', action: '导入资料' },
  { index: 3, title: '一键真实检测', description: '真实执行验证探针，阻断 / 未执行等状态如实展示', path: '/campaigns', action: '进入运行中心' },
  { index: 4, title: '查看结论与证据', description: '已确认问题附完整证据链与复现路径，可直接验收', path: '/findings', action: '查看问题清单' },
];

/** 首次使用引导：四步闭环（接入 → 资料 → 检测 → 结论），全部指向真实页面。 */
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
