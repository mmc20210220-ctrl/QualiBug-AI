import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { saveSettings, saveEnvConfig } from '../api/client';

export function Settings() {
  const [params] = useSearchParams();
  const project = params.get('project') || 'real_project_demo';
  const [llmUrl, setLlmUrl] = useState('https://api.deepseek.com/v1');
  const [llmModel, setLlmModel] = useState('deepseek-chat');
  const [llmKey, setLlmKey] = useState('');
  const [llmStatus, setLlmStatus] = useState('');
  const [envName, setEnvName] = useState('MES-BugLab Test');
  const [baseUrl, setBaseUrl] = useState('http://127.0.0.1:8000/api');
  const [timeout, setTimeout_] = useState('30');
  const [envStatus, setEnvStatus] = useState('');

  const saveLLM = async () => {
    setLlmStatus('验证中...');
    try { await saveSettings({ llm_base_url: llmUrl, llm_model: llmModel, llm_api_key: llmKey }); setLlmStatus('✓ LLM Online'); }
    catch (e: any) { setLlmStatus(`✗ ${e.message}`); }
  };
  const saveEnv = async () => {
    setEnvStatus('保存中...');
    try { await saveEnvConfig({ project_id: project, target_environment: envName, base_url: baseUrl, request_timeout_seconds: parseInt(timeout) }); setEnvStatus('✓ 已保存'); }
    catch (e: any) { setEnvStatus(`✗ ${e.message}`); }
  };

  return (
    <div>
      <div className="page-header"><div><h1>设置</h1><p>LLM 引擎配置 · 目标环境管理 · 扫描策略</p></div></div>

      <div style={{ maxWidth: 672 }}>
        <div className="section-card">
          <h2>LLM 引擎配置</h2>
          <div className="form-group"><label className="form-label">API Base URL</label><input className="form-input form-input-mono" value={llmUrl} onChange={e => setLlmUrl(e.target.value)} /></div>
          <div className="form-group"><label className="form-label">Model</label><input className="form-input" value={llmModel} onChange={e => setLlmModel(e.target.value)} /></div>
          <div className="form-group"><label className="form-label">API Key</label><input className="form-input" type="password" value={llmKey} onChange={e => setLlmKey(e.target.value)} placeholder="sk-..." /></div>
          <button onClick={saveLLM} className="btn btn-primary">验证并保存</button>
          {llmStatus && <p className="text-muted mt-2" style={{ fontSize: 12 }}>{llmStatus}</p>}
        </div>

        <div className="section-card">
          <h2>目标环境</h2>
          <div className="form-group"><label className="form-label">环境名称</label><input className="form-input" value={envName} onChange={e => setEnvName(e.target.value)} /></div>
          <div className="form-group"><label className="form-label">Base URL</label><input className="form-input form-input-mono" value={baseUrl} onChange={e => setBaseUrl(e.target.value)} /></div>
          <div className="form-group"><label className="form-label">超时 (秒)</label><input className="form-input" type="number" value={timeout} onChange={e => setTimeout_(e.target.value)} style={{ width: 96 }} /></div>
          <button onClick={saveEnv} className="btn btn-secondary">保存环境配置</button>
          {envStatus && <p className="text-muted mt-2" style={{ fontSize: 12 }}>{envStatus}</p>}
        </div>

        <div className="section-card">
          <h2>系统信息</h2>
          <div style={{ fontSize: 12 }}>
            {[
              { k: '产品版本', v: 'QualiBug Enterprise v2' },
              { k: '服务状态', v: '🟢 运行中 · 行为空间持续监控' },
              { k: '审计链路', v: '完整 · 全部可追溯' },
            ].map(r => (
              <div key={r.k} className="flex items-center justify-between mb-1" style={{ padding: '6px 0', borderBottom: '1px solid var(--line)' }}>
                <span className="text-muted">{r.k}</span><span style={{ color: r.k === '审计链路' || r.k === '数据安全' ? 'var(--success)' : 'var(--ink)', fontWeight: 500 }}>{r.v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
