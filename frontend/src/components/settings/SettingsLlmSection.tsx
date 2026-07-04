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
    <div className="section-card">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">引擎配置</span>
          <h2>智能引擎</h2>
        </div>
        <span className={`status status-${llmStateTone}`}>{llmHealthy ? '已连接' : '待验证'}</span>
      </div>
      <div className="settings-card-note">仅在输入新值时更新配置，并立即做一次连接验证。</div>
      <div className="form-group"><label className="form-label">引擎接入地址</label><input className="form-input form-input-mono" value={llmUrl} onChange={(e) => onLlmUrlChange(e.target.value)} placeholder="留空则保持当前配置"/></div>
      <div className="form-group"><label className="form-label">引擎模型名称</label><input className="form-input" value={llmModel} onChange={(e) => onLlmModelChange(e.target.value)} placeholder="留空则保持当前配置"/></div>
      <div className="form-group"><label className="form-label">引擎访问密钥</label><input className="form-input" type="password" value={llmKey} onChange={(e) => onLlmKeyChange(e.target.value)} placeholder="留空则保持当前配置"/></div>
      <div className="settings-llm-actions">
        <button onClick={onSaveAndVerify} className="btn btn-primary">保存并验证</button>
        <span className={`settings-llm-state tone-${llmStateTone}`} title={llmError}>
          <span className="settings-llm-dot" />
          {llmStateText}
        </span>
      </div>
    </div>
  );
}

