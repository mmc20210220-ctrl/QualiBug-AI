import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { deleteKnowledge, ingestKnowledge } from '../api/client';
import { useKnowledgeData } from '../api/data';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';
import { formatBeijingDateTime } from '../lib/time';

type IngestResult = {
  ingest_status?: string;
  knowledge_updated?: boolean;
  message?: string;
};

type FeedbackTone = 'success' | 'danger' | 'warning' | 'info';
type MaterialsFilter = 'all' | KnowledgeType;
type MaterialsSort = 'latest' | 'oldest' | 'name_asc' | 'size_desc';
type BatchViewFilter = 'all' | 'attention' | 'done';
type UploadOutcome = {
  fileName: string;
  file: File;
  type: string;
  tone: FeedbackTone;
  message: string;
  knowledgeUpdated: boolean;
};
type BatchUploadState = {
  total: number;
  completed: number;
  currentFileName: string;
  results: UploadOutcome[];
};

type KnowledgeSourceRow = {
  source_id: string;
  filename: string;
  source_type: string;
  size_bytes: number;
  status: string;
  uploaded_at: string;
};

type KnowledgeType = 'prd' | 'openapi' | 'db_design' | 'business_rules' | 'ui_design' | 'test_data' | 'config' | 'deploy' | 'mobile_android' | 'mobile_ios' | 'other';

const KNOWLEDGE_TYPE_OPTIONS: Array<{ value: KnowledgeType; label: string }> = [
  { value: 'prd', label: 'PRD 需求文档' },
  { value: 'openapi', label: 'API 接口文档' },
  { value: 'db_design', label: '数据库设计' },
  { value: 'business_rules', label: '业务规则' },
  { value: 'ui_design', label: 'UI 设计稿' },
  { value: 'test_data', label: '测试数据' },
  { value: 'config', label: '配置文件' },
  { value: 'deploy', label: '部署文档' },
  { value: 'mobile_android', label: 'Android 安装包 (.apk)' },
  { value: 'mobile_ios', label: 'iOS 安装包 (.ipa)' },
  { value: 'other', label: '其他资料' },
];

const SORT_OPTIONS: Array<{ value: MaterialsSort; label: string }> = [
  { value: 'latest', label: '最近导入' },
  { value: 'oldest', label: '最早导入' },
  { value: 'name_asc', label: '文件名 A-Z' },
  { value: 'size_desc', label: '文件大小' },
];

const SOURCE_TYPE_LABELS: Record<string, string> = {
  prd: 'PRD 文档',
  openapi: '接口文档',
  collaboration_document: '业务/DB 文档',
  other_document: '其他文档',
  historical_bug: '历史缺陷',
  database_schema: '数据库设计',
  mobile_android: 'Android 安装包',
  mobile_ios: 'iOS 安装包',
};

const SOURCE_STATUS_LABELS: Record<string, { label: string; tone: string }> = {
  active: { label: '已生效', tone: 'success' },
  processing: { label: '处理中', tone: 'warning' },
  failed: { label: '失败', tone: 'danger' },
};
const EXECUTABLE_SOURCE_TYPES = new Set(['prd', 'openapi', 'database_schema', 'collaboration_document', 'historical_bug']);

function normalizeUploadError(error: unknown) {
  const rawMessage = error instanceof Error ? error.message : '资料导入失败';
  if (rawMessage.includes('未选择有效项目')) {
    return '未选择有效项目，请重新选择项目';
  }
  if (rawMessage.includes('PROJECT_NOT_FOUND') || rawMessage.includes('项目') && rawMessage.includes('不存在')) {
    return '当前项目不存在，请重新选择项目';
  }
  if (rawMessage.includes('UNSUPPORTED_SOURCE_TYPE')) {
    return '当前资料类型暂不支持，请改用 PRD、接口文档、数据库设计、Android/iOS 安装包等类型导入';
  }
  if (rawMessage.includes('MISSING_CONTENT') || rawMessage.includes('DECODE_FAILED')) {
    return '文件读取失败，请重新选择文件后重试';
  }
  if (rawMessage.includes('/api/knowledge/ingest') || rawMessage.includes('API 404: NOT_FOUND')) {
    return '当前环境未启用资料导入接口';
  }
  if (rawMessage.includes('INGEST_FAILED') || rawMessage.includes('资料导入失败')) {
    return '资料导入失败，请检查文件内容后重试';
  }
  return '资料导入失败，请稍后重试';
}

function formatUploadedAt(value: string) {
  return formatBeijingDateTime(value, '未记录');
}

function formatSourceType(value: string) {
  return SOURCE_TYPE_LABELS[String(value || '').trim()] || value || '未分类';
}

function formatSourceStatus(value: string) {
  return SOURCE_STATUS_LABELS[String(value || '').trim()] || { label: value || '未知', tone: 'neutral' };
}

function getOutcomePrefix(tone: FeedbackTone) {
  if (tone === 'success') return '✓';
  if (tone === 'danger') return '✗';
  if (tone === 'warning') return '!';
  return 'i';
}

function getOutcomeLabel(tone: FeedbackTone) {
  if (tone === 'success') return '已导入';
  if (tone === 'danger') return '失败';
  if (tone === 'warning') return '待重试';
  return '已复用';
}

function isRetryableOutcome(item: UploadOutcome) {
  return item.tone === 'danger' || item.tone === 'warning';
}

function isCompletedOutcome(item: UploadOutcome) {
  return item.tone === 'success' || item.tone === 'info';
}

export function EnterpriseMaterials() {
  usePageTitle('企业资料');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { sources, loading, error, refetch } = useKnowledgeData(project);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [status, setStatus] = useState('');
  const [statusTone, setStatusTone] = useState<FeedbackTone>('info');
  const [selectedType, setSelectedType] = useState<KnowledgeType>('prd');
  const [activeFilter, setActiveFilter] = useState<MaterialsFilter>('all');
  const [sortMode, setSortMode] = useState<MaterialsSort>('latest');
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeSourceRow | null>(null);
  const [deleteConfirmText, setDeleteConfirmText] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [batchUploadState, setBatchUploadState] = useState<BatchUploadState | null>(null);
  const [batchView, setBatchView] = useState<BatchViewFilter>('all');
  const fileRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  function inferKnowledgeType(file: File): KnowledgeType {
    const normalizedName = file.name.toLowerCase();
    // API documents — markdown API specs, OpenAPI, Swagger, Postman, HAR etc.
    if (normalizedName.includes('openapi') || normalizedName.includes('swagger') ||
        normalizedName.includes('api') || normalizedName.includes('接口') ||
        ['.yaml', '.yml', '.har', '.proto', '.graphql', '.gql'].some(s => normalizedName.endsWith(s)) ||
        (normalizedName.endsWith('.json') && !normalizedName.includes('config') && !normalizedName.includes('package'))) {
      return 'openapi';
    }
    // Database design
    if (['.sql', '.dbml', '.prisma', '.schema', '.ddl', '.dml'].some(s => normalizedName.endsWith(s)) ||
        normalizedName.includes('schema') || normalizedName.includes('database') || normalizedName.includes('er图') || normalizedName.includes('数据') && normalizedName.includes('库')) {
      return 'db_design';
    }
    // Business rules
    if (normalizedName.includes('business') || normalizedName.includes('rule') || normalizedName.includes('业务规则') ||
        normalizedName.includes('validation') || normalizedName.includes('constraint')) {
      return 'business_rules';
    }
    // UI design
    if (['.svg', '.fig', '.sketch', '.psd', '.xd'].some(s => normalizedName.endsWith(s)) ||
        normalizedName.includes('ui') || normalizedName.includes('ux') || normalizedName.includes('design') ||
        normalizedName.includes('设计') || normalizedName.includes('原型') || normalizedName.includes('mock')) {
      return 'ui_design';
    }
    // Mobile App
    if (['.apk'].some(s => normalizedName.endsWith(s))) return 'mobile_android';
    if (['.ipa'].some(s => normalizedName.endsWith(s))) return 'mobile_ios';
    // Test data
    if (['.csv', '.xlsx', '.xls'].some(s => normalizedName.endsWith(s)) ||
        normalizedName.includes('test_data') || normalizedName.includes('fixture') || normalizedName.includes('seed') ||
        normalizedName.includes('测试数据') || normalizedName.includes('样本')) {
      return 'test_data';
    }
    // Config files
    if (['.env', '.toml', '.ini', '.conf', '.cfg'].some(s => normalizedName.endsWith(s)) ||
        normalizedName.includes('config') || normalizedName.includes('env') || normalizedName.includes('settings') ||
        normalizedName.includes('配置') || normalizedName.includes('参数')) {
      return 'config';
    }
    // Deploy docs
    if (['Dockerfile', 'docker-compose', '.k8s', '.helm'].some(s => normalizedName.includes(s.toLowerCase())) ||
        normalizedName.includes('deploy') || normalizedName.includes('部署') || normalizedName.includes('k8s') ||
        normalizedName.includes('ci') || normalizedName.includes('cd')) {
      return 'deploy';
    }
    // PRD
    if (normalizedName.includes('prd') || normalizedName.includes('需求') || normalizedName.includes('requirement') ||
        normalizedName.includes('spec') || ['.doc', '.docx', '.pdf'].some(s => normalizedName.endsWith(s))) {
      return 'prd';
    }
    return 'other';
  }

  const uploadFile = useCallback(async (file: File, type: string): Promise<UploadOutcome> => {
    if (!project) {
      return {
        fileName: file.name,
        file,
        type,
        tone: 'info',
        message: '未选择项目，无法导入资料',
        knowledgeUpdated: false,
      };
    }
    try {
      const result = await ingestKnowledge(project, file, type) as IngestResult;
      const knowledgeUpdated = result.knowledge_updated !== false && result.ingest_status !== 'saved_only';
      const duplicate = result.ingest_status === 'duplicate';
      const message = knowledgeUpdated
        ? (result.message || (duplicate ? `${file.name} 已复用现有资料` : `${file.name} 已导入知识库`))
        : `${file.name} 已上传，但资料索引未更新，请稍后重试`;
      const tone = !knowledgeUpdated ? 'warning' : duplicate ? 'info' : 'success';
      return {
        fileName: file.name,
        file,
        type,
        tone,
        message,
        knowledgeUpdated,
      };
    } catch (error: unknown) {
      const message = normalizeUploadError(error);
      return {
        fileName: file.name,
        file,
        type,
        tone: 'danger',
        message,
        knowledgeUpdated: false,
      };
    }
  }, [project]);

  const uploadFiles = useCallback(async (items: Array<{ file: File; type: string }>) => {
    if (!items.length || uploading) return;
    if (!project) {
      setStatusTone('info');
      setStatus('未选择项目，无法导入资料');
      toast.show('未选择项目，无法导入资料', 'info');
      return;
    }

    setUploading(true);
    try {
      const results: UploadOutcome[] = [];
      setBatchView('all');
      setBatchUploadState({
        total: items.length,
        completed: 0,
        currentFileName: items[0]?.file.name || '',
        results: [],
      });
      for (const [index, item] of items.entries()) {
        setStatusTone('info');
        setStatus(`正在导入 ${index + 1}/${items.length}：${item.file.name}`);
        const outcome = await uploadFile(item.file, item.type);
        results.push(outcome);
        setBatchUploadState({
          total: items.length,
          completed: index + 1,
          currentFileName: items[index + 1]?.file.name || '',
          results: [...results],
        });
      }

      const failedCount = results.filter((item) => item.tone === 'danger').length;
      const warningCount = results.filter((item) => item.tone === 'warning').length;
      const successCount = results.filter((item) => item.tone === 'success').length;
      const duplicateCount = results.filter((item) => item.tone === 'info').length;
      const updatedCount = results.filter((item) => item.knowledgeUpdated).length;
      const summary = results.length === 1
        ? results[0].message
        : failedCount === results.length
          ? `${results.length} 个文件导入失败，请检查文件内容后重试`
          : `本次共处理 ${results.length} 个文件：成功 ${successCount} 个，复用 ${duplicateCount} 个，待重试 ${warningCount} 个，失败 ${failedCount} 个`;
      const tone: FeedbackTone = failedCount === results.length
        ? 'danger'
        : failedCount > 0 || warningCount > 0
          ? 'warning'
          : 'success';
      const prefix = tone === 'danger' ? '✗' : tone === 'warning' ? '!' : '✓';

      setStatusTone(tone);
      setStatus(`${prefix} ${summary}`);
      toast.show(summary, tone);
      if (fileRef.current) fileRef.current.value = '';
      if (updatedCount > 0) {
        setTimeout(() => refetch(), 300);
      }
    } finally {
      setUploading(false);
    }
  }, [project, refetch, toast, uploadFile, uploading]);

  const handleUpload = async (type: KnowledgeType) => {
    const input = fileRef.current;
    if (!input?.files?.length) {
      const option = KNOWLEDGE_TYPE_OPTIONS.find((item) => item.value === type);
      setSelectedType(type);
      setStatusTone('info');
      setStatus(`已切换为${option?.label || '当前类型'}，请先选择文件或直接拖拽上传`);
      return;
    }
    await uploadFiles(Array.from(input.files).map((file) => ({ file, type })));
  };

  const handleFileSelection = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    if (!files.length || uploading) return;
    void uploadFiles(files.map((file) => ({ file, type: selectedType || inferKnowledgeType(file) })));
  };

  useEffect(() => {
    const preventWindowDrop = (event: DragEvent) => {
      event.preventDefault();
    };

    window.addEventListener('dragover', preventWindowDrop);
    window.addEventListener('drop', preventWindowDrop);
    return () => {
      window.removeEventListener('dragover', preventWindowDrop);
      window.removeEventListener('drop', preventWindowDrop);
    };
  }, []);

  useEffect(() => {
    if (!deleteTarget) return;
    const handleKeydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !deleting) {
        resetDeleteDialog();
      }
    };
    window.addEventListener('keydown', handleKeydown);
    return () => window.removeEventListener('keydown', handleKeydown);
  }, [deleteTarget, deleting]);

  const switchKnowledgeType = (type: KnowledgeType) => {
    setSelectedType(type);
    void handleUpload(type);
  };

  const resetDeleteDialog = () => {
    setDeleteTarget(null);
    setDeleteConfirmText('');
    setDeleting(false);
  };

  const handleDelete = useCallback(async () => {
    if (!deleteTarget || !project) return;
    if (deleteConfirmText.trim() !== deleteTarget.filename) {
      toast.show('请先准确输入文件名，再执行删除', 'warning');
      setStatusTone('warning');
      setStatus('请先准确输入完整文件名，再执行删除');
      return;
    }
    setDeleting(true);
    try {
      await deleteKnowledge(project, deleteTarget.source_id);
      const message = `${deleteTarget.filename} 已永久删除`;
      toast.show(message, 'success');
      setStatusTone('success');
      setStatus(`✓ ${message}`);
      resetDeleteDialog();
      setTimeout(() => refetch(), 300);
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : '资料删除失败，请稍后重试';
      toast.show(message, 'danger');
      setStatusTone('danger');
      setStatus(`✗ ${message}`);
      setDeleting(false);
    }
  }, [deleteConfirmText, deleteTarget, project, refetch, toast]);

  const handleDrop = async (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(false);
    if (uploading) return;
    const files = Array.from(event.dataTransfer.files || []);
    if (!files.length) return;
    await uploadFiles(files.map((file) => ({ file, type: selectedType || inferKnowledgeType(file) })));
  };

  const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!dragActive) setDragActive(true);
  };

  const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDragActive(false);
  };

  const displaySources = sources.filter((item) => item.status !== 'deleted');
  const filteredSources = displaySources.filter((item) => activeFilter === 'all' ? true : item.source_type === activeFilter);
  const filteredAndSortedSources = [...filteredSources].sort((left, right) => {
    if (sortMode === 'oldest') return String(left.uploaded_at || '').localeCompare(String(right.uploaded_at || ''));
    if (sortMode === 'name_asc') return String(left.filename || '').localeCompare(String(right.filename || ''), 'zh-CN');
    if (sortMode === 'size_desc') return Number(right.size_bytes || 0) - Number(left.size_bytes || 0);
    return String(right.uploaded_at || '').localeCompare(String(left.uploaded_at || ''));
  });
  const activeCount = displaySources.filter((item) => item.status === 'active').length;
  const processingCount = displaySources.filter((item) => item.status === 'processing').length;
  const failedCount = displaySources.filter((item) => item.status === 'failed').length;
  const executableCount = displaySources.filter((item) => EXECUTABLE_SOURCE_TYPES.has(String(item.source_type || '').trim())).length;
  const prdCount = displaySources.filter((item) => String(item.source_type || '').trim() === 'prd').length;
  const apiCount = displaySources.filter((item) => String(item.source_type || '').trim() === 'openapi').length;
  const dbCount = displaySources.filter((item) => ['database_schema', 'db_design'].includes(String(item.source_type || '').trim())).length;
  const latestUploadedAt = displaySources
    .map((item) => item.uploaded_at)
    .filter(Boolean)
    .sort()
    .at(-1) || '';
  const topSourceTypes = Object.entries(displaySources.reduce<Record<string, number>>((acc, item) => {
    const key = formatSourceType(item.source_type);
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {})).sort((left, right) => right[1] - left[1]).slice(0, 4);
  const parseHeadline = displaySources.length === 0
    ? '当前还没有进入知识中心的项目资料'
    : failedCount > 0
      ? '当前有资料导入失败，需先修复后再进入执行'
      : processingCount > 0
        ? '资料正在进入知识中心，建议等待解析完成后再运行'
        : executableCount > 0
          ? '当前资料已形成可执行上下文，可直接进入运行中心'
          : '当前已导入资料，但可执行上下文仍需继续补齐';
  const parseDescription = displaySources.length === 0
    ? '建议至少先导入 PRD、接口文档或数据库设计，再进入运行中心。'
    : executableCount > 0
      ? `已识别 ${executableCount} 份可直接支撑执行的核心资料，其中 PRD ${prdCount} 份、接口文档 ${apiCount} 份、数据库资料 ${dbCount} 份。`
      : '当前资料更多停留在补充信息层，还缺少能直接驱动执行的核心来源。';
  const helperText = status || (
    selectedType === 'prd'
      ? '用于沉淀需求背景、业务流程与验收口径'
      : selectedType === 'openapi'
        ? '用于沉淀接口定义、字段约束与调用规则'
        : selectedType === 'db_design'
          ? '用于沉淀表结构、字段关系与索引设计'
          : selectedType === 'business_rules'
            ? '用于沉淀业务规则、审批口径与计算逻辑'
            : selectedType === 'ui_design'
              ? '用于沉淀页面结构、交互说明与视觉稿'
              : selectedType === 'test_data'
                ? '用于沉淀测试样例、模拟数据与校验口径'
                : selectedType === 'config'
                  ? '用于沉淀环境配置、参数说明与依赖关系'
                  : selectedType === 'deploy'
                    ? '用于沉淀部署流程、发布步骤与运维说明'
                    : selectedType === 'mobile_android'
                      ? '上传 .apk 安装包，自动提取权限和深链接，连接模拟器后可进行动态 UI 检测'
                      : selectedType === 'mobile_ios'
                        ? '上传 .ipa 安装包，自动解析 Info.plist，需 macOS 连接模拟器进行动态检测'
                        : '用于补充对扫描判断有帮助的项目资料'
  );

  const formatSize = (b: number) => {
    if (b >= 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
    if (b >= 1024) return `${Math.round(b / 1024)} KB`;
    return `${b} B`;
  };
  const batchSummary = batchUploadState
    ? `本次共 ${batchUploadState.total} 个文件，已处理 ${batchUploadState.completed} 个`
    : '';
  const currentUploadLabel = batchUploadState && batchUploadState.completed < batchUploadState.total
    ? `当前处理：${batchUploadState.currentFileName || '准备中'}`
    : '';
  const batchResults = batchUploadState?.results || [];
  const retryableItems = useMemo(
    () => batchUploadState?.results.filter(isRetryableOutcome) || [],
    [batchUploadState?.results],
  );
  const retryableCount = retryableItems.length;
  const completedCount = batchResults.filter(isCompletedOutcome).length;
  const visibleBatchResults = batchResults.filter((item) => {
    if (batchView === 'attention') return isRetryableOutcome(item);
    if (batchView === 'done') return isCompletedOutcome(item);
    return true;
  });
  const handleRetryBatch = useCallback(async () => {
    if (!retryableItems.length || uploading) return;
    await uploadFiles(retryableItems.map((item) => ({ file: item.file, type: item.type })));
  }, [retryableItems, uploadFiles, uploading]);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>企业资料</h1>
          <p>按资料类型导入企业文档，进入知识中心沉淀结构化线索</p>
        </div>
      </div>
      <div className={`materials-feedback materials-feedback-${statusTone}`}>
        <strong>{status ? '当前回执' : '操作提示'}</strong>
        <span>{status || '支持上传、筛选和删除资料；关键结果会在这里持续显示，避免提示一闪而过。'}</span>
      </div>
      <div className="customer-summary-grid mb-4">
        {[
          { label: '资料总数', value: displaySources.length, tone: displaySources.length > 0 ? 'primary' : 'neutral', note: '已进入项目知识中心的资料数量' },
          { label: '已生效', value: activeCount, tone: activeCount > 0 ? 'success' : 'neutral', note: '当前可被扫描链直接消费的资料' },
          { label: '可执行资料', value: executableCount, tone: executableCount > 0 ? 'success' : 'warning', note: executableCount > 0 ? '已具备驱动测试执行的核心上下文' : '建议补齐 PRD / API / DB 设计' },
          { label: '异常资料', value: failedCount, tone: failedCount > 0 ? 'danger' : 'neutral', note: failedCount > 0 ? '导入失败会直接影响后续执行' : '当前无失败资料' },
        ].map((item) => (
          <article key={item.label} className={`customer-summary-card tone-${item.tone}`}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>{item.note}</small>
          </article>
        ))}
      </div>
      <section className="section-card mb-4">
        <div className="product-section-head">
          <div>
            <span className="panel-kicker">解析结果</span>
            <h2>{parseHeadline}</h2>
            <p>{parseDescription}</p>
          </div>
        </div>
        <div className="customer-secondary-grid">
          <article className="customer-secondary-card">
            <span className="customer-value-kicker">核心资料覆盖</span>
            <h3>{prdCount > 0 && apiCount > 0 ? 'PRD + API 已就绪' : '核心资料待补齐'}</h3>
            <p>PRD {prdCount} 份，接口文档 {apiCount} 份，数据库资料 {dbCount} 份。核心资料越完整，运行中心越容易形成真实可执行路径。</p>
          </article>
          <article className={`customer-secondary-card${processingCount > 0 || failedCount > 0 ? ' muted' : ''}`}>
            <span className="customer-value-kicker">解析状态</span>
            <h3>{processingCount > 0 ? '仍在处理' : failedCount > 0 ? '存在失败项' : '当前稳定可用'}</h3>
            <p>处理中 {processingCount} 份，失败 {failedCount} 份。只有真实进入知识中心并成功解析的资料，才会被后续扫描和证据链消费。</p>
          </article>
          <article className="customer-secondary-card">
            <span className="customer-value-kicker">资料结构</span>
            <h3>{topSourceTypes.length > 0 ? topSourceTypes.map(([label]) => label).join('、') : '等待导入'}</h3>
            <p>{topSourceTypes.length > 0 ? topSourceTypes.map(([label, count]) => `${label} ${count} 份`).join('，') : '导入后会在这里展示当前项目的资料分布。'}</p>
          </article>
        </div>
      </section>
      {batchUploadState && (
        <section className="materials-batch-panel">
          <div className="materials-batch-head">
            <div>
              <strong>批量导入进度</strong>
              <p>{batchSummary}</p>
            </div>
            <div className="materials-batch-head-actions">
              {retryableCount > 0 && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm materials-batch-retry"
                  onClick={() => void handleRetryBatch()}
                  disabled={uploading}
                >
                  {uploading ? '重试中' : `重试失败项 (${retryableCount})`}
                </button>
              )}
              <span className={`status status-${uploading ? 'info' : statusTone}`}>
                {uploading ? '处理中' : '本次结果'}
              </span>
            </div>
          </div>
          <div className="materials-batch-progress" aria-hidden="true">
            <span
              className="materials-batch-progress-bar"
              style={{ width: `${Math.max(0, Math.min(100, Math.round((batchUploadState.completed / Math.max(1, batchUploadState.total)) * 100)))}%` }}
            />
          </div>
          <p className="materials-batch-current">{uploading ? currentUploadLabel : '本次批量导入已处理完成，可直接查看逐项结果。'}</p>
          {batchResults.length > 0 && (
            <div className="materials-batch-filter-bar" role="tablist" aria-label="批量导入结果筛选">
              <button
                type="button"
                className={`materials-batch-filter-chip${batchView === 'all' ? ' is-active' : ''}`}
                onClick={() => setBatchView('all')}
              >
                全部结果 ({batchResults.length})
              </button>
              <button
                type="button"
                className={`materials-batch-filter-chip${batchView === 'attention' ? ' is-active' : ''}`}
                onClick={() => setBatchView('attention')}
              >
                待处理 ({retryableCount})
              </button>
              <button
                type="button"
                className={`materials-batch-filter-chip${batchView === 'done' ? ' is-active' : ''}`}
                onClick={() => setBatchView('done')}
              >
                已完成 ({completedCount})
              </button>
            </div>
          )}
          <div className="materials-batch-list" role="list" aria-label="批量导入结果">
            {visibleBatchResults.map((item, index) => (
              <div key={`${item.fileName}-${index}`} className="materials-batch-item" role="listitem">
                <div className="materials-batch-file">
                  <span className={`status status-${item.tone}`}>{getOutcomeLabel(item.tone)}</span>
                  <strong title={item.fileName}>{item.fileName}</strong>
                </div>
                <span className="materials-batch-message">
                  {getOutcomePrefix(item.tone)} {item.message}
                </span>
                {isRetryableOutcome(item) && (
                  <div className="materials-batch-item-actions">
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm materials-batch-item-retry"
                      onClick={() => void uploadFiles([{ file: item.file, type: item.type }])}
                      disabled={uploading}
                    >
                      {uploading ? '处理中' : '仅重试此文件'}
                    </button>
                  </div>
                )}
              </div>
            ))}
            {visibleBatchResults.length === 0 && (
              <div className="materials-batch-empty">
                {batchView === 'attention' ? '当前没有待处理文件。' : batchView === 'done' ? '当前没有已完成文件。' : '当前没有可展示的批量结果。'}
              </div>
            )}
          </div>
        </section>
      )}

      {/* Upload Zone */}
      <div
        className={`upload-zone materials-upload-zone mb-4${dragActive ? ' is-active' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        <input ref={fileRef} type="file" accept="*" multiple className="materials-upload-input" id="file-upload" onChange={handleFileSelection} />
        <label htmlFor="file-upload" className="materials-upload-label">
          <div className="upload-icon materials-upload-badge">资料</div>
          <p className="upload-text">
            选择一个或多个文件后，系统会按当前类型顺序入库<br />
            支持需求、接口、数据库、规则、设计、测试、配置与部署资料批量导入
          </p>
          <p className="materials-upload-hint">
            {dragActive ? '松开后立即上传当前类型资料' : '先选择资料类型，再点击上传或直接拖入文件'}
          </p>
        </label>
        <div className="upload-actions">
          {KNOWLEDGE_TYPE_OPTIONS.map((option) => (
            <button
              key={option.value}
              onClick={() => switchKnowledgeType(option.value)}
              disabled={uploading}
              className={`btn ${selectedType === option.value ? 'btn-primary' : 'btn-secondary'}`}
            >
              {uploading && selectedType === option.value ? '上传中' : option.label}
            </button>
          ))}
        </div>
        <p className="materials-upload-helper">{helperText}</p>
      </div>

      {/* Source Table */}
      <div className="materials-panel">
        <div className="materials-panel-head">
          已导入资料 ({filteredSources.length}/{displaySources.length})
        </div>
        {!loading && (
          <div className="materials-summary-bar">
            <div className="materials-summary-item">
              <span>资料总数</span>
              <strong>{displaySources.length}</strong>
            </div>
            <div className="materials-summary-item">
              <span>当前生效</span>
              <strong>{activeCount}</strong>
            </div>
            <div className="materials-summary-item">
              <span>最近导入</span>
              <strong>{latestUploadedAt ? formatUploadedAt(latestUploadedAt) : '暂无记录'}</strong>
            </div>
          </div>
        )}
        {!loading && displaySources.length > 0 && (
          <div className="materials-filter-bar">
            {([{ value: 'all', label: '全部资料' }, ...KNOWLEDGE_TYPE_OPTIONS] as Array<{ value: MaterialsFilter; label: string }>).map((option) => (
              <button
                key={option.value}
                type="button"
                className={`materials-filter-chip${activeFilter === option.value ? ' is-active' : ''}`}
                onClick={() => setActiveFilter(option.value)}
              >
                {option.label}
              </button>
            ))}
            <div className="materials-toolbar-spacer" />
            <select className="materials-sort-select" value={sortMode} onChange={(event) => setSortMode(event.target.value as MaterialsSort)}>
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
        )}
        {loading && (
          <div className="materials-loading-state">
            <div className="spinner spinner-centered" />
          </div>
        )}
        {!loading && filteredAndSortedSources.length > 0 && (
          <div className="overflow-x-auto materials-table-wrap">
            <table className="data-table materials-table">
              <colgroup>
                <col className="materials-col-file" />
                <col className="materials-col-type" />
                <col className="materials-col-size" />
                <col className="materials-col-status" />
                <col className="materials-col-time" />
                <col className="materials-col-action" />
              </colgroup>
              <thead><tr><th>文件名</th><th>类型</th><th className="text-right">大小</th><th>状态</th><th>导入时间</th><th className="materials-table-action-head">操作</th></tr></thead>
              <tbody>
                {filteredAndSortedSources.map(s => (
                  <tr key={s.source_id}>
                    <td className="materials-table-file-cell">
                      <div className="materials-file-cell">
                        <strong className="materials-file-name" title={s.filename}>{s.filename}</strong>
                      </div>
                    </td>
                    <td className="materials-table-type-cell"><span className="materials-type-chip">{formatSourceType(s.source_type)}</span></td>
                    <td className="font-mono text-right materials-table-size-cell">{formatSize(s.size_bytes ?? 0)}</td>
                    <td className="materials-table-status-cell"><span className={`status status-${formatSourceStatus(s.status).tone}`}>{formatSourceStatus(s.status).label}</span></td>
                    <td className="text-muted materials-table-time-cell">{formatUploadedAt(s.uploaded_at ?? '')}</td>
                    <td className="materials-table-action-cell">
                      <button
                        type="button"
                        className="btn btn-danger btn-table-danger"
                        onClick={() => {
                          setDeleteTarget(s as KnowledgeSourceRow);
                          setDeleteConfirmText('');
                        }}
                        disabled={uploading || deleting}
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!loading && displaySources.length > 0 && filteredAndSortedSources.length === 0 && (
          <section className="findings-empty-state compact">
            <span className="findings-empty-kicker">筛选结果</span>
            <h3>当前没有匹配资料</h3>
            <p>当前筛选条件下没有资料，请切换查看其他资料类型。</p>
          </section>
        )}
        {!loading && displaySources.length === 0 && (
          <section className="findings-empty-state compact">
            <span className="findings-empty-kicker">当前空态</span>
            <h3>{!project ? '未选择项目' : error ? '资料列表暂时读取失败' : '当前还没有导入资料'}</h3>
            <p>{!project ? '选择项目后可继续管理资料导入与删除。' : error || '导入业务资料、接口资料或数据库资料后，这里会形成可筛选的资料清单。'}</p>
          </section>
        )}
      </div>
      {deleteTarget && (
        <div className="danger-dialog-backdrop" role="presentation" onClick={() => !deleting && resetDeleteDialog()}>
          <div className="danger-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-material-title" onClick={(event) => event.stopPropagation()}>
            <div className="danger-dialog-head">
              <span className="danger-dialog-kicker">高风险操作</span>
              <h3 id="delete-material-title">确认删除当前资料</h3>
              <p>删除后，当前资料将从项目知识库移除，后续扫描与证据整理将不再使用这份文件。</p>
            </div>
            <div className="danger-dialog-body">
              <div className="danger-dialog-file">
                <span>待删除文件</span>
                <strong>{deleteTarget.filename}</strong>
                <small>{formatSourceType(deleteTarget.source_type)} · {formatSize(deleteTarget.size_bytes)} · {formatUploadedAt(deleteTarget.uploaded_at)}</small>
              </div>
              <div className="danger-dialog-warning">删除后不可恢复，后续扫描、证据链整理与资料补全将不再引用这份文件。</div>
              <label className="form-label" htmlFor="delete-material-confirm">
                请输入完整文件名以确认删除
              </label>
              <input
                id="delete-material-confirm"
                className="form-input"
                value={deleteConfirmText}
                onChange={(event) => setDeleteConfirmText(event.target.value)}
                placeholder={deleteTarget.filename}
                autoFocus
              />
              <p className="danger-dialog-hint">只有输入与上方完全一致的文件名后，才允许执行删除。</p>
            </div>
            <div className="danger-dialog-actions">
              <button type="button" className="btn btn-secondary" onClick={resetDeleteDialog} disabled={deleting}>
                取消
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => void handleDelete()}
                disabled={deleting || deleteConfirmText.trim() !== deleteTarget.filename}
              >
                {deleting ? '删除中...' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default EnterpriseMaterials;
