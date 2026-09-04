import { useSearchParams } from 'react-router-dom';
import { RequirementIntelligence } from './RequirementIntelligence';
import { TestIntelligence } from './TestIntelligence';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import './Analyze.css';

type AnalyzeView = 'requirements' | 'test-targets';

export function Analyze() {
  usePageTitle('分析');
  const [params, setParams] = useSearchParams();
  const { navigateToProjectPath } = useProjectNavigation();
  const project = params.get('project')?.trim() || '';
  const activeView: AnalyzeView = params.get('view') === 'test-targets' ? 'test-targets' : 'requirements';

  const selectView = (view: AnalyzeView) => {
    const next = new URLSearchParams(params);
    next.set('view', view);
    setParams(next, { replace: true });
  };

  return (
    <div className="analyze-workspace">
      <header className="analyze-header">
        <div>
          <span className="panel-kicker">Analyze · 软件理解</span>
          <h1>先理解软件应该如何工作，再决定需要验证什么</h1>
          <p>
            QualiBug 从当前项目已接入的需求、接口、设计、历史问题与其他企业资料中提取可追溯事实，
            审查冲突与缺口，并把可证明的业务语义继续转化为验证目标。
          </p>
        </div>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => navigateToProjectPath('/materials', project)}
        >
          管理资料来源
        </button>
      </header>

      <nav className="analyze-tabs" aria-label="分析工作区">
        <button
          type="button"
          className={activeView === 'requirements' ? 'active' : ''}
          onClick={() => selectView('requirements')}
        >
          <strong>Requirements</strong>
          <span>冲突、缺失、歧义与就绪状态</span>
        </button>
        <button
          type="button"
          className={activeView === 'test-targets' ? 'active' : ''}
          onClick={() => selectView('test-targets')}
        >
          <strong>Test Targets</strong>
          <span>必须验证什么，以及当前验证设计</span>
        </button>
      </nav>

      <section className="analyze-surface">
        {activeView === 'requirements' ? <RequirementIntelligence /> : <TestIntelligence />}
      </section>
    </div>
  );
}

export default Analyze;
