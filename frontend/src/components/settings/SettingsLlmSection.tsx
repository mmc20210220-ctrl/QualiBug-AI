type SettingsLlmSectionProps = {
  llmStateTone: string;
  llmHealthy: boolean;
  llmStateText: string;
  llmError: string;
  llmUrl: string;
  llmModel: string;
  llmKey: string;
  onLlmUrlChange: (value: string) => void;
  onLlmModelChange: (value: string) => void;
  onLlmKeyChange: (value: string) => void;
  onSaveAndVerify: () => void;
};

export function SettingsLlmSection({
  llmStateTone,
  llmHealthy,
  llmStateText,
  llmError,
  llmUrl,
  llmModel,
  llmKey,
  onLlmUrlChange,
  onLlmModelChange,
  onLlmKeyChange,
  onSaveAndVerify,
}: SettingsLlmSectionProps) {
  return (
    <details className="section-card">
      <summary>
        <strong>高级：智能引擎接入</strong>
        <span className={`status status-${llmStateTone}`}>{llmHealthy ? '已连接' : '待验证'}</span>
      </summary>
      <div className="settings-card-note settings-mt-10">
        模型供应商、地址和模型名称属于部署级能力，不应成为每个客户项目的日常维护项。仅在首次部署、更换供应商或连接故障时修改。
      </div>
      <div className="form-group"><label className="form-label">引擎接入地址</label><input className="form-input form-input-mono" value={llmUrl} onChange={(e) => onLlmUrlChange(e.target.value)} placeholder="留空则保持当前配置"/></div>
      <div className="form-group"><label className="form-label">引擎模型名称</label><input className="form-input" value={llmModel} onChange={(e) => onLlmModelChange(e.target.value)} placeholder="留空则保持当前配置"/></div>
      <div className="form-group"><label className="form-label">引擎访问密钥</label><input className="form-input" type="password" value={llmKey} onChange={(e) => onLlmKeyChange(e.target.value)} placeholder="留空则保持当前配置"/></div>
      <div className="settings-llm-actions">
        <button onClick={onSaveAndVerify} className="btn btn-secondary">保存并验证</button>
        <span className={`settings-llm-state tone-${llmStateTone}`} title={llmError}>
          <span className="settings-llm-dot" />
          {llmStateText}
        </span>
      </div>
    </details>
  );
}
