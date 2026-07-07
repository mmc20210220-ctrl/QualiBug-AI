import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  listEnterpriseSourceAssets,
  registerEnterpriseSourceAsset,
  type SourceAssetSummary,
} from '../api/enterprise';
import { useToast } from '../components/useToast';
import { usePageTitle } from '../lib/page-title';

const MAX_SOURCE_BYTES = 5_000_000;
const SOURCE_TYPES = [
  { value: 'openapi', label: 'API / OpenAPI 文档' },
  { value: 'prd', label: '需求资料' },
  { value: 'database_schema', label: '数据库设计' },
  { value: 'collaboration_document', label: '协作资料' },
  { value: 'other_document', label: '其他文本资料' },
] as const;

function shortHash(value: string): string {
  return value.length > 18 ? `${value.slice(0, 18)}…` : value;
}

function formatDate(value: string): string {
  if (!value) return '未记录';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function EnterpriseSourceAssets() {
  usePageTitle('来源资产');
  const [params] = useSearchParams();
  const toast = useToast();
  const project = params.get('project')?.trim() || '';
  const fileRef = useRef<HTMLInputElement>(null);
  const [assets, setAssets] = useState<SourceAssetSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [sourceId, setSourceId] = useState('');
  const [sourceType, setSourceType] = useState<(typeof SOURCE_TYPES)[number]['value']>('openapi');
  const [content, setContent] = useState('');
  const [filename, setFilename] = useState('');
  const [externalRef, setExternalRef] = useState('');
  const [error, setError] = useState('');

  const sortedAssets = useMemo(
    () => [...assets].sort((left, right) => right.updated_at_utc.localeCompare(left.updated_at_utc)),
    [assets],
  );

  const refresh = useCallback(async () => {
    if (!project) {
      setAssets([]);
      return;
    }
    setLoading(true);
    try {
      setAssets(await listEnterpriseSourceAssets(project));
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '来源资产加载失败';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => { void refresh(); }, [refresh]);

  const selectFile = useCallback(async (file: File) => {
    if (file.size > MAX_SOURCE_BYTES) {
      setError('单个来源资产不能超过 5 MB。请拆分资料或通过连接器快照导入。');
      return;
    }
    try {
      const text = await file.text();
      if (!text.trim()) {
        setError('文件没有可读取的文本内容，不能登记为来源资产。');
        return;
      }
      setContent(text);
      setFilename(file.name);
      setSourceId((current) => current || file.name);
      setError('');
    } catch {
      setError('无法读取该文件。来源资产目前只接受可读文本资料。');
    }
  }, []);

  const register = useCallback(async () => {
    if (!project) {
      setError('请先选择客户项目。');
      return;
    }
    if (!sourceId.trim() || !content.trim()) {
      setError('来源标识和文本内容均为必填项。');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const manifest = await registerEnterpriseSourceAsset({
        project_id: project,
        source_id: sourceId.trim(),
        source_type: sourceType,
        content,
        filename: filename.trim(),
        external_ref: externalRef.trim(),
        origin: 'manual_upload',
      });
      toast.show(`来源版本已登记：${manifest.source_version_id || manifest.source_hash.slice(0, 12)}`, 'success');
      setContent('');
      setFilename('');
      setExternalRef('');
      if (fileRef.current) fileRef.current.value = '';
      await refresh();
    } catch (caught: unknown) {
      const message = caught instanceof Error ? caught.message : '来源登记失败';
      setError(message);
      toast.show(message, 'danger');
    } finally {
      setSaving(false);
    }
  }, [content, externalRef, filename, project, refresh, sourceId, sourceType, toast]);

  if (!project) {
    return <section className="state-panel"><div className="state-panel-badge">客户选择</div><h2>请先选择客户项目</h2><p>来源资产必须被隔离到具体项目，不能在未确定客户项目时登记或复用。</p></section>;
  }

  return (
    <div>
      <div className="page-header">
        <div><h1>来源资产</h1><p>登记不可变文本版本，生成可追溯哈希，供受控 Campaign 与变更影响分析使用。</p></div>
        <button type="button" className="btn btn-secondary" onClick={() => void refresh()} disabled={loading}>{loading ? '刷新中' : '刷新资产'}</button>
      </div>

      <section className="card mb-4">
        <h2>登记来源版本</h2>
        <p className="muted">资料内容、来源标识和版本哈希会被绑定。上传凭据、令牌或真实用户数据前应先脱敏。</p>
        <div className="settings-grid">
          <label className="form-field"><span>来源标识</span><input value={sourceId} onChange={(event) => setSourceId(event.target.value)} placeholder="稳定来源标识，例如 api-contract" /></label>
          <label className="form-field"><span>资料类型</span><select value={sourceType} onChange={(event) => setSourceType(event.target.value as (typeof SOURCE_TYPES)[number]['value'])}>{SOURCE_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
          <label className="form-field"><span>外部引用（可选）</span><input value={externalRef} onChange={(event) => setExternalRef(event.target.value)} placeholder="文档、提交或工单的稳定引用" /></label>
        </div>
        <div className="settings-actions">
          <input ref={fileRef} type="file" accept=".json,.yaml,.yml,.md,.txt,.sql,.graphql,.gql,.xml,.csv" onChange={(event) => { const file = event.target.files?.[0]; if (file) void selectFile(file); }} />
          <span className="muted">{filename ? `已选择：${filename}` : '或直接粘贴文本内容'}</span>
        </div>
        <label className="form-field"><span>原始文本内容</span><textarea value={content} onChange={(event) => setContent(event.target.value)} rows={12} placeholder="粘贴 API 文档、需求、数据库设计或其他已脱敏的文本资料" /></label>
        <div className="settings-actions"><button type="button" className="btn btn-primary" onClick={() => void register()} disabled={saving}>{saving ? '登记中' : '登记不可变来源版本'}</button></div>
      </section>

      {error && <section className="state-panel"><div className="state-panel-badge">需要处理</div><h2>来源资产未登记</h2><p>{error}</p></section>}

      <section className="card">
        <h2>已登记来源资产</h2>
        {!loading && sortedAssets.length === 0 && <p className="muted">当前项目暂无来源资产。完成登记后可前往“运行中心”绑定来源并发起受控执行。</p>}
        {sortedAssets.length > 0 && <div className="table-wrap"><table><thead><tr><th>来源标识</th><th>类型</th><th>当前版本</th><th>哈希</th><th>版本数</th><th>更新时间</th></tr></thead><tbody>{sortedAssets.map((asset) => <tr key={asset.source_id}><td>{asset.source_id}</td><td>{asset.source_type}</td><td>{asset.latest_version_id}</td><td title={asset.latest_source_hash}>{shortHash(asset.latest_source_hash)}</td><td>{asset.version_count}</td><td>{formatDate(asset.updated_at_utc)}</td></tr>)}</tbody></table></div>}
      </section>
    </div>
  );
}

export default EnterpriseSourceAssets;
