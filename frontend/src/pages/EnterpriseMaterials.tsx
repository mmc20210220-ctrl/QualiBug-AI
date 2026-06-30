import { useState, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ingestKnowledge } from '../api/client';
import { useKnowledgeData } from '../api/data';
import { useToast } from '../components/Toast';

export function EnterpriseMaterials() {
  const [params] = useSearchParams();
  const project = params.get('project') || 'real_project_demo';
  const { sources, loading, refetch } = useKnowledgeData(project);
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  const handleUpload = async (type: string) => {
    const input = fileRef.current;
    if (!input?.files?.length) return;
    const file = input.files[0];
    setUploading(true);
    setStatus(`上传中: ${file.name}...`);
    try {
      await ingestKnowledge(project, file, type);
      setStatus(`✓ ${file.name} 上传成功`);
      toast.show(`${file.name} 已导入知识库`, 'success');
      input.value = '';
      setTimeout(() => refetch(), 1000);
    } catch (e: any) {
      setStatus(`✗ 上传失败`);
      toast.show(`上传失败: ${e.message}`, 'danger');
    } finally {
      setUploading(false);
    }
  };

  const displaySources = sources.length > 0 ? sources : [
    { source_id: 'demo-1', filename: 'PRD_v2.1.md', source_type: 'PRD', status: 'active', size_bytes: 45820, uploaded_at: '2026-06-30' },
    { source_id: 'demo-2', filename: 'openapi_mes_v3.yaml', source_type: 'OpenAPI', status: 'active', size_bytes: 124500, uploaded_at: '2026-06-30' },
  ];

  const formatSize = (b: number) => b > 102400 ? `${(b/1024).toFixed(0)}KB` : `${b} B`;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>企业资料</h1>
          <p>全格式兼容 · 拖入即解析 · 自动构建行为模型</p>
        </div>
      </div>

      {/* Upload Zone */}
      <div className="upload-zone mb-4">
        <input ref={fileRef} type="file" accept="*" style={{ display: 'none' }} id="file-upload" />
        <label htmlFor="file-upload" style={{ cursor: 'pointer', display: 'block' }}>
          <div className="upload-icon">📄</div>
          <p className="upload-text">
            全格式兼容 · Office/PDF/图片/流程图/思维导图/CAD/代码/压缩包/数据库导出<br />
            任意企业文件拖入即解析
          </p>
        </label>
        <div className="upload-actions">
          <button onClick={() => handleUpload('prd')} disabled={uploading} className="btn btn-primary">
            {uploading ? '⏳ 上传中' : 'PRD 文档'}
          </button>
          <button onClick={() => handleUpload('openapi')} disabled={uploading} className="btn btn-secondary">
            OpenAPI 规范
          </button>
          <button onClick={() => handleUpload('business_doc')} disabled={uploading} className="btn btn-secondary">
            业务文档
          </button>
        </div>
        {status && <p className="text-muted mt-3" style={{ fontSize: 12 }}>{status}</p>}
      </div>

      {/* Source Table */}
      <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
        <div style={{ padding: '12px 20px', background: '#f8fafc', borderBottom: '1px solid var(--line)', fontSize: 12, fontWeight: 700, color: 'var(--muted)' }}>
          已导入资料 ({displaySources.length})
        </div>
        {loading && (
          <div style={{ textAlign: 'center', padding: 32 }}>
            <div className="spinner" style={{ margin: '0 auto' }} />
          </div>
        )}
        {!loading && (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead><tr><th>文件名</th><th>类型</th><th style={{ textAlign: 'right' }}>大小</th><th>状态</th><th>导入时间</th></tr></thead>
              <tbody>
                {displaySources.map(s => (
                  <tr key={s.source_id}>
                    <td style={{ fontWeight: 500 }}>{s.filename}</td>
                    <td className="text-muted">{s.source_type}</td>
                    <td className="font-mono text-right">{formatSize(s.size_bytes)}</td>
                    <td><span className="status status-success">{s.status}</span></td>
                    <td className="text-muted">{s.uploaded_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
