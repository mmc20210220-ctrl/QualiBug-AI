import { useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ingestKnowledge } from '../api/client';
import { useToast } from '../components/useToast';
import { RequirementIntelligence } from './RequirementIntelligence';
import { TestIntelligence } from './TestIntelligence';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import './Analyze.css';

type AnalyzeView = 'requirements' | 'test-targets';

const PRD_ACCEPT = '.pdf,.doc,.docx,.md,.txt,.ppt,.pptx';

export function Analyze() {
  usePageTitle('分析');
  const [params, setParams] = useSearchParams();
  const { navigateToProjectPath } = useProjectNavigation();
  const toast = useToast();
  const project = params.get('project')?.trim() || '';
  const activeView: AnalyzeView = params.get('view') === 'test-targets' ? 'test-targets' : 'requirements';
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
      <header className="analyze-header">
        <div>
          <span className="panel-kicker">Analyze · 软件理解</span>
          <h1>先理解软件应该如何工作，再决定需要验证什么</h1>
          <p>
            只给 QualiBug 一份 PRD 也可以开始：系统先审查需求冲突、定义缺失与业务歧义，
            再把有来源证据的业务语义转化为验证目标；连接测试环境之后才进入真实 Verify。
          </p>
        </div>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => navigateToProjectPath('/materials', project)}
        >
          管理全部资料
        </button>
      </header>

      <section className="analyze-ingest" aria-label="上传 PRD 开始分析">
        <div
          className={`analyze-dropzone${!project ? ' disabled' : ''}${uploading ? ' busy' : ''}`}
          onDragOver={(event) => event.preventDefault()}
          onDrop={handleDrop}
        >
          <div className="analyze-drop-icon" aria-hidden="true">＋</div>
          <div className="analyze-drop-copy">
            <span>从 PRD 开始</span>
            <strong>{uploading ? '正在导入 PRD…' : '拖入一份 PRD，立即开始需求分析'}</strong>
            <p>{project ? '支持 PDF / Word / Markdown / Text / PowerPoint；上传后进入同一企业知识主链。' : '先在右上角选择客户项目，然后即可上传 PRD。'}</p>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!project || uploading}
            onClick={() => uploadInputRef.current?.click()}
          >
            {uploading ? '正在上传…' : '选择 PRD'}
          </button>
          <input ref={uploadInputRef} type="file" accept={PRD_ACCEPT} hidden onChange={handleFileChange} />
        </div>
        <div className="analyze-ingest-next">
          <span>Analyze 到 Verify</span>
          <strong>PRD 分析本身就有价值，不要求先有可执行环境</strong>
          <p>需求结论与 Test Targets 可以先交付；只有真实 Runtime Grounding 完成后，Verify 才会显示 Agent Execution 与 Evidence。</p>
        </div>
      </section>

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
        {activeView === 'requirements'
          ? <RequirementIntelligence key={`requirements:${project}:${analysisVersion}`} />
          : <TestIntelligence key={`targets:${project}:${analysisVersion}`} />}
      </section>
    </div>
  );
}

export default Analyze;
