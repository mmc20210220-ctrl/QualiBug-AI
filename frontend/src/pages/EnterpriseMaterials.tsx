import { useState, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useKnowledgeData } from '../api/data';
import { useToast } from '../components/Toast';

export function EnterpriseMaterials() {
  const [params] = useSearchParams();
  const project = params.get('project') || 'real_project_demo';
  const { sources, loading, refetch } = useKnowledgeData(project);
  const [uploading, setUploading] = useState('');
  const [status, setStatus] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const pendingType = useRef<string>('');
  const toast = useToast();

  const triggerUpload = (type: string) => {
    pendingType.current = type;
    fileRef.current?.click();
  };

  const handleFileChange = async () => {
    const input = fileRef.current;
    if (!input?.files?.length) return;
    const file = input.files[0];
    const type = pendingType.current;

    setUploading(type);
    setStatus(`上传中: ${file.name}...`);
    try {
      const reader = new FileReader();
      await new Promise<void>((resolve, reject) => {
        reader.onload = async () => {
          try {
            const b64 = (reader.result as string).split(',')[1];
            const resp = await fetch('/api/knowledge/ingest', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ project_id: project, type, filename: file.name, content: b64 }),
            });
            const d = await resp.json();
            if (!d.ok) throw new Error(d.message || 'fail');
            resolve();
          } catch (e) { reject(e); }
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      setStatus(`✓ ${file.name} 上传成功`);
      toast.show(`${file.name} 已导入知识库`, 'success');
      input.value = '';
      setTimeout(() => refetch(), 1000);
    } catch (e: any) {
      setStatus(`✗ 上传失败`);
      toast.show(`上传失败: ${e.message}`, 'danger');
    } finally {
      setUploading('');
    }
  };

  const formatSize = (b: number) => b > 102400 ? `${(b/1024).toFixed(0)}KB` : `${b} B`;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>企业资料</h1>
          <p>全格式兼容 · 拖入即解析 · 自动构建行为模型</p>
        </div>
      </div>

      <div className="upload-zone mb-4">
        <input ref={fileRef} type="file" accept="*" style={{ display: 'none' }} onChange={handleFileChange} />
        <div className="upload-icon">📄</div>
        <p className="upload-text">
          全格式兼容 · Office/PDF/图片/流程图/思维导图/CAD/代码/压缩包/数据库导出<br />
          任意企业文件拖入即解析
        </p>
        <div className="upload-actions">
          <button onClick={() => triggerUpload('prd')} disabled={uploading !== ''} className="btn btn-primary">
            {uploading === 'prd' ? '⏳ 上传中' : 'PRD 文档'}
          </button>
          <button onClick={() => triggerUpload('openapi')} disabled={uploading !== ''} className="btn btn-secondary">
            {uploading === 'openapi' ? '⏳ 上传中' : 'OpenAPI 规范'}
          </button>
          <button onClick={() => triggerUpload('business_doc')} disabled={uploading !== ''} className="btn btn-secondary">
            {uploading === 'business_doc' ? '⏳ 上传中' : '业务文档'}
          </button>
        </div>
        {status && <p className="text-muted mt-3" style={{ fontSize: 12 }}>{status}</p>}
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', overflow: 'hidden' }}>
        <div style={{ padding: '12px 20px', background: '#f8fafc', borderBottom: '1px solid var(--line)', fontSize: 12, fontWeight: 700, color: 'var(--muted)' }}>
          已导入资料 ({sources.length})
        </div>
        {loading && (
          <div style={{ textAlign: 'center', padding: 32 }}>
            <div className="spinner" style={{ margin: '0 auto' }} />
          </div>
        )}
        {!loading && sources.length === 0 && (
          <div style={{ textAlign: 'center', padding: '32px 20px' }}>
            <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 8 }}>暂无导入资料</p>
            <p style={{ color: 'var(--subtle)', fontSize: 11 }}>上传 PRD、OpenAPI 或业务文档以构建行为模型</p>
          </div>
        )}
        {!loading && sources.length > 0 && (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead><tr><th>文件名</th><th>类型</th><th style={{ textAlign: 'right' }}>大小</th><th>状态</th><th>导入时间</th></tr></thead>
              <tbody>
                {sources.map(s => (
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
