import { useCallback, useEffect, useMemo, useState } from 'react';
import { getKnowledgeAsset } from '../../api/client';
import {
  ingestKnowledgeFiles,
  KNOWLEDGE_UPLOAD_ACCEPT,
  type KnowledgeIngestResult,
} from '../../api/knowledge-ingest';
import { EnterpriseUnderstandingReceipt } from './EnterpriseUnderstandingReceipt';
import { asArray, asRecord, asText } from '../../lib/value-guards';

type WorkspaceOption = {
  id: string;
  label: string;
};

type KnowledgeSource = {
  source_id: string;
  filename: string;
  source_type: string;
  status: string;
  version: number;
  parse_status: string;
};

type SettingsCustomerSectionProps = {
  workspaceLabel: string;
  workspacesCount: number;
  project: string;
  workspaceOptions: WorkspaceOption[];
  importId: string;
  wsStatus: string;
  workspaceLoadFailed?: boolean;
  onWorkspaceChange: (value: string) => void;
  onRefresh: () => void;
  onImportIdChange: (value: string) => void;
  onCreateWorkspace: () => void;
};

function sourceTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    prd: '产品需求',
    mrd: '市场需求',
    openapi: '接口契约',
    markdown_api: '接口文档',
    postman: 'Postman 集合',
    database_schema: '数据库结构',
    db_field_dictionary: '字段字典',
    permission_matrix: '权限矩阵',
    historical_bug: '历史缺陷',
    ticket: '工单资料',
    business_rules: '业务规则',
    uiux_spec: 'UI/UX 规范',
    uiux_svg: '界面设计图',
    test_data: '测试数据',
    config: '系统配置',
    deploy: '部署资料',
    application_log: '应用日志',
    har: '网络轨迹',
    feishu_document: '飞书文档',
    confluence_document: 'Confluence 文档',
    collaboration_document: '协作文档',
    other_document: '其他资料',
  };
  return labels[value] || value || '后台识别中';
}

function knowledgeSources(payload: unknown): KnowledgeSource[] {
  const asset = asRecord(asRecord(payload).knowledge_asset);
  const values = [...asArray(asset.sources), ...asArray(asset.source_inventory)];
  const byId = new Map<string, KnowledgeSource>();
  for (const value of values) {
    const row = asRecord(value);
    const sourceId = asText(row.source_id) || asText(row.id);
    const status = (asText(row.status) || 'active').toLowerCase();
    if (!sourceId || status === 'deleted') continue;
    byId.set(sourceId, {
      source_id: sourceId,
      filename: asText(row.filename) || asText(row.original_name) || asText(row.name) || sourceId,
      source_type: asText(row.source_type) || asText(row.type),
      status,
      version: Number(row.version || 1) || 1,
      parse_status: asText(asRecord(row.parse).parse_status) || asText(row.parse_status),
    });
  }
  return [...byId.values()].sort((left, right) => left.filename.localeCompare(right.filename));
}

function uploadSummary(results: KnowledgeIngestResult[]): string {
  const labels = [...new Set(results.map((result) => sourceTypeLabel(result.doc_type || '')).filter(Boolean))];
  const triggered = results.some((result) => result.auto_scan === 'triggered');
  const recognized = labels.length > 0 ? `，自动识别为 ${labels.join('、')}` : '';
  return `✓ 已导入 ${results.length} 份资料${recognized}${triggered ? '；后台已开始统一理解和增量验证' : '；后台已完成入库并等待统一分析'}`;
}

export function SettingsCustomerSection({
  workspaceLabel,
  workspacesCount,
  project,
  workspaceOptions,
  importId,
  wsStatus,
  workspaceLoadFailed = false,
  onWorkspaceChange,
  onRefresh,
  onImportIdChange,
  onCreateWorkspace,
}: SettingsCustomerSectionProps) {
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [knowledgePayload, setKnowledgePayload] = useState<unknown>(null);
  const [knowledgeStatus, setKnowledgeStatus] = useState('');
  const [loadingSources, setLoadingSources] = useState(false);
  const [uploading, setUploading] = useState(false);

  const refreshSources = useCallback(async () => {
    if (!project) {
      setSources([]);
      setKnowledgePayload(null);
      return;
    }
    setLoadingSources(true);
    try {
      const payload = await getKnowledgeAsset(project);
      setKnowledgePayload(payload);
      setSources(knowledgeSources(payload));
    } catch (caught) {
      setSources([]);
      setKnowledgePayload(null);
      setKnowledgeStatus(caught instanceof Error ? `✗ 资料状态读取失败：${caught.message}` : '✗ 资料状态读取失败');
    } finally {
      setLoadingSources(false);
    }
  }, [project]);

  useEffect(() => {
    setKnowledgeStatus('');
    void refreshSources();
  }, [refreshSources]);

  const sourceTypeCount = useMemo(
    () => new Set(sources.map((source) => source.source_type).filter(Boolean)).size,
    [sources],
  );

  const handleFilesSelected = useCallback(async (files: File[]) => {
    if (!project) {
      setKnowledgeStatus('✗ 请先选择客户项目。');
      return;
    }
    if (files.length === 0) return;
    setUploading(true);
    setKnowledgeStatus(`正在导入 ${files.length} 份原始资料，后台会自动分类、去重和理解…`);
    try {
      const results = await ingestKnowledgeFiles(project, files);
      setKnowledgeStatus(uploadSummary(results));
      await refreshSources();
    } catch (caught) {
      setKnowledgeStatus(caught instanceof Error ? `✗ ${caught.message}` : '✗ 资料导入失败');
    } finally {
      setUploading(false);
    }
  }, [project, refreshSources]);

  return (
    <div className="section-card">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">项目接入</span>
          <h2>客户与企业资料</h2>
        </div>
      </div>
      <div className="settings-card-note">
        选择客户后直接上传原始资料。用户不需要判断资料类型、选择解析策略、维护版本或逐项绑定场景。
      </div>

      <div className="settings-compact-row">
        <div className="form-group settings-flex-grow">
          <label className="form-label">当前客户</label>
          <select className="form-input" value={project} onChange={(event) => onWorkspaceChange(event.target.value)}>
            <option value="">{workspacesCount ? '请选择客户' : (workspaceLoadFailed ? '客户列表加载失败' : '暂无客户')}</option>
            {workspaceOptions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </div>
        <button onClick={onRefresh} className="btn btn-secondary settings-btn-compact">重新同步</button>
      </div>
      <p className="settings-hint">当前：{workspaceLabel}</p>

      <div className="section-card settings-mt-10">
        <div className="settings-card-head">
          <div>
            <span className="panel-kicker">原始资料入口</span>
            <h3>导入企业资料</h3>
            <p className="settings-card-sub">可一次选择多份 PRD、接口文档、数据库设计、权限资料、历史缺陷、UI 设计或协作文档。</p>
          </div>
          <strong className={sources.length > 0 ? 'is-positive' : 'is-neutral'}>
            {loadingSources ? '同步中' : `${sources.length} 份已入库`}
          </strong>
        </div>

        <label className="form-field">
          <span>选择文件后立即导入</span>
          <input
            className="form-input"
            type="file"
            multiple
            accept={KNOWLEDGE_UPLOAD_ACCEPT}
            disabled={!project || uploading}
            onChange={(event) => {
              const files = Array.from(event.currentTarget.files || []);
              event.currentTarget.value = '';
              void handleFilesSelected(files);
            }}
          />
          <small className="muted">
            不需要选择资料类型或点击二次确认。后台自动识别内容、合并重复版本、检测冲突，并在整批资料入库后统一启动理解。
          </small>
        </label>

        {knowledgeStatus && <p className="settings-inline-feedback" role="status">{knowledgeStatus}</p>}

        <EnterpriseUnderstandingReceipt
          payload={knowledgePayload}
          loading={loadingSources}
          hasSources={sources.length > 0}
          project={project}
          onAuthorityDecision={() => {
            void refreshSources();
          }}
        />

        {sources.length > 0 && (
          <details className="settings-auth-section settings-mt-10">
            <summary>
              <strong>查看后台识别的资料来源</strong>
              <span className="muted">{sources.length} 份资料 · {sourceTypeCount} 类来源</span>
            </summary>
            <div className="settings-info-list settings-mt-10">
              {sources.map((source) => (
                <div key={source.source_id} className="settings-info-row">
                  <span>{source.filename}</span>
                  <strong>{sourceTypeLabel(source.source_type)} · v{source.version}{source.parse_status ? ` · ${source.parse_status}` : ''}</strong>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>

      <details className="settings-auth-section settings-mt-10">
        <summary><strong>创建新客户项目</strong> <span className="muted">仅首次接入新客户时使用</span></summary>
        <div className="settings-compact-row settings-mt-10">
          <div className="form-group settings-flex-grow">
            <label className="form-label">公司名称</label>
            <input className="form-input" value={importId} onChange={(event) => onImportIdChange(event.target.value)} placeholder="输入公司名称" />
          </div>
          <button onClick={onCreateWorkspace} className="btn btn-primary settings-btn-compact">创建并切换</button>
        </div>
      </details>
      {wsStatus && <p className="settings-inline-feedback">{wsStatus}</p>}
    </div>
  );
}
