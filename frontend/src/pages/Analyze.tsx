import { useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ingestKnowledge } from '../api/client';
import { useToast } from '../components/useToast';
import { RequirementIntelligence } from './RequirementIntelligence';
import { TestIntelligence } from './TestIntelligence';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import './Analyze.css';

type AnalyzeView = 'requirements' | 'test-targets' | 'test-data';

const PRD_ACCEPT = '.pdf,.doc,.docx,.md,.txt,.ppt,.pptx';

export function Analyze() {
  usePageTitle('Knowledge');
  const [params, setParams] = useSearchParams();
  const { navigateToProjectPath } = useProjectNavigation();
  const toast = useToast();
  const project = params.get('project')?.trim() || '';
  const taskId = params.get('task')?.trim() || '';
  const goal = params.get('goal')?.trim() || '';
  const requestedView = params.get('view');
  const activeView: AnalyzeView = requestedView === 'test-targets' || requestedView === 'test-data' ? requestedView : 'requirements';
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [analysisVersion, setAnalysisVersion] = useState(0);

  const selectView = (view: AnalyzeView) => {
    const next = new URLSearchParams(params);
    next.set('view', view);
    setParams(next, { replace: true });
  };

  const uploadPrd = async (file: File) => {
    if (!project) {
      toast.show('请先选择客户项目，再上传 PRD。', 'warning');
      return;
    }
    if (!file.name.trim()) return;
    setUploading(true);
    try {
      await ingestKnowledge(project, file, 'prd');
      toast.show(`${file.name} 已加入企业知识库，正在读取最新需求分析结果。`, 'success');
      setAnalysisVersion((value) => value + 1);
      selectView('requirements');
    } catch (error: unknown) {
      toast.show(error instanceof Error ? error.message : 'PRD 上传失败', 'danger');
    } finally {
      setUploading(false);
      if (uploadInputRef.current) uploadInputRef.current.value = '';
    }
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) void uploadPrd(file);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (uploading) return;
    const file = event.dataTransfer.files?.[0];
    if (file) void uploadPrd(file);
  };

  return (
    <div className="analyze-workspace">
      {taskId && <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/verify', project, `task=${encodeURIComponent(taskId)}&section=deliverables`)}>← 返回任务交付物</button>}
      <header className="analyze-header">
        <div>
          <span className="panel-kicker">Knowledge · Understanding</span>
          <h1>需求审查与测试设计</h1>
          <p>
            查看已有资料形成的审查结果与测试设计，按需展开来源证据。这里展示项目共享知识，实际测试结果请在任务中查看。
          </p>
        </div>
        <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/materials', project)}>管理全部资料</button>
      </header>

      {goal && (
        <section className="analyze-goal-context" aria-label="当前 Agent 任务上下文">
          <span>Current goal</span>
          <strong>{goal}</strong>
          <p>该文本只作为当前工作目标上下文；需求真值与 Test Targets 仍只来自已接入资料和后端 Intelligence API。</p>
        </section>
      )}

      <details className="analyze-upload-disclosure"><summary>补充需求资料</summary><section className="analyze-ingest" aria-label="上传 PRD 开始分析">
        <div
          className={`analyze-dropzone${!project ? ' disabled' : ''}${uploading ? ' busy' : ''}`}
          onDragOver={(event) => event.preventDefault()}
          onDrop={handleDrop}
        >
          <div className="analyze-drop-icon" aria-hidden="true">＋</div>
          <div className="analyze-drop-copy">
            <span>Give the agent context</span>
            <strong>{uploading ? '正在导入 PRD…' : '拖入一份 PRD，让 QualiBug 先理解系统'}</strong>
            <p>{project ? '支持 PDF / Word / Markdown / Text / PowerPoint；上传后进入同一企业知识资产主链。' : '先在右上角选择客户项目，然后即可上传 PRD。'}</p>
          </div>
          <button type="button" className="btn btn-primary" disabled={!project || uploading} onClick={() => uploadInputRef.current?.click()}>
            {uploading ? '正在上传…' : '选择 PRD'}
          </button>
          <input ref={uploadInputRef} type="file" accept={PRD_ACCEPT} hidden onChange={handleFileChange} />
        </div>
        <div className="analyze-ingest-next">
          <span>Understanding before execution</span>
          <strong>只有 PRD，也可以先交付需求风险和验证目标</strong>
          <p>没有 Runtime Grounding 时不会声称已经执行测试；Knowledge 与真实 Verify 是连续主链，但不是同一种真值。</p>
        </div>
      </section></details>

      <nav className="analyze-tabs" aria-label="Knowledge 工作区">
        <button type="button" className={activeView === 'requirements' ? 'active' : ''} onClick={() => selectView('requirements')}>
          <strong>需求审查</strong>
          <span>冲突、缺失、歧义与需求就绪状态</span>
        </button>
        <button type="button" className={activeView === 'test-targets' ? 'active' : ''} onClick={() => selectView('test-targets')}>
          <strong>测试设计与数据要求</strong>
          <span>Agent 需要验证什么，以及当前验证设计</span>
        </button>
        <button type="button" className={activeView === 'test-data' ? 'active' : ''} onClick={() => selectView('test-data')}>
          <strong>测试数据</strong>
          <span>数据约束、准备状态与执行前缺口</span>
        </button>
      </nav>

      <section className="analyze-surface">
        {activeView === 'requirements'
          ? <RequirementIntelligence key={`requirements:${project}:${analysisVersion}`} />
          : <TestIntelligence key={`${activeView}:${project}:${analysisVersion}`} />}
      </section>
    </div>
  );
}

export default Analyze;
