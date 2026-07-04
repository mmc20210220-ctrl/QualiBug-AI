export type DbOption = {
  v: string;
  l: string;
  p: number;
  c: 'relational' | 'nosql';
};

type DbCredentialPanelProps = {
  dbOpen: boolean;
  credentialSummary: string;
  dbType: string;
  dbHost: string;
  dbPort: string;
  dbUser: string;
  dbPass: string;
  dbName: string;
  dbTestLoad: boolean;
  dbTestOk: boolean;
  dbTestMsg: string;
  dbTestHintText: string;
  dbOptions: DbOption[];
  getDbDefaultPort: (type: string) => string;
  onToggleOpen: () => void;
  onDbTypeChange: (value: string) => void;
  onDbHostChange: (value: string) => void;
  onDbPortChange: (value: string) => void;
  onDbUserChange: (value: string) => void;
  onDbPassChange: (value: string) => void;
  onDbNameChange: (value: string) => void;
  onTest: () => void;
  onApplyCredentialRef: () => void;
};

export function DbCredentialPanel({
  dbOpen,
  credentialSummary,
  dbType,
  dbHost,
  dbPort,
  dbUser,
  dbPass,
  dbName,
  dbTestLoad,
  dbTestOk,
  dbTestMsg,
  dbTestHintText,
  dbOptions,
  getDbDefaultPort,
  onToggleOpen,
  onDbTypeChange,
  onDbHostChange,
  onDbPortChange,
  onDbUserChange,
  onDbPassChange,
  onDbNameChange,
  onTest,
  onApplyCredentialRef,
}: DbCredentialPanelProps) {
  return (
    <div className="settings-db-panel">
      <div className="settings-db-head">
        <span>数据库连接</span>
        <button onClick={onToggleOpen} className="btn btn-secondary settings-btn-mini">{dbOpen ? '收起' : credentialSummary ? '已配置 · 修改' : '配置数据库'}</button>
      </div>
      {credentialSummary && !dbOpen && <div className="settings-db-summary">{credentialSummary}</div>}
      {dbOpen && (
        <div className="settings-db-grid">
          <div>
            <label className="form-label">数据库类型</label>
            <select className="form-input" value={dbType} onChange={(e) => onDbTypeChange(e.target.value)}>
              <optgroup label="关系型">{dbOptions.filter((item) => item.c === 'relational').map((item) => <option key={item.v} value={item.v}>{item.l}</option>)}</optgroup>
              <optgroup label="非关系型">{dbOptions.filter((item) => item.c === 'nosql').map((item) => <option key={item.v} value={item.v}>{item.l}</option>)}</optgroup>
            </select>
          </div>
          <div><label className="form-label">主机地址</label><input className="form-input form-input-mono" value={dbHost} onChange={(e) => onDbHostChange(e.target.value)} placeholder="localhost"/></div>
          <div><label className="form-label">端口</label><input className="form-input" value={dbPort} onChange={(e) => onDbPortChange(e.target.value)} placeholder={getDbDefaultPort(dbType)}/></div>
          <div><label className="form-label">用户名</label><input className="form-input" value={dbUser} onChange={(e) => onDbUserChange(e.target.value)} placeholder="用户名"/></div>
          <div><label className="form-label">密码</label><input className="form-input" type="password" value={dbPass} onChange={(e) => onDbPassChange(e.target.value)} placeholder="密码"/></div>
          <div><label className="form-label">数据库名</label><input className="form-input" value={dbName} onChange={(e) => onDbNameChange(e.target.value)} placeholder="可选"/></div>
          <div className="settings-db-actions">
            <button disabled={dbTestLoad || !dbHost.trim()} onClick={onTest} className="btn btn-secondary settings-btn-mini">{dbTestLoad ? '测试中...' : '测试连接'}</button>
            {dbTestOk && <button onClick={onApplyCredentialRef} className="btn btn-primary settings-btn-mini">保存凭证引用</button>}
            {dbTestMsg
              ? <span className={`settings-db-feedback ${dbTestOk ? 'success' : 'danger'}`}>{dbTestMsg}</span>
              : <span className="settings-db-hint">{dbTestHintText}</span>}
          </div>
        </div>
      )}
    </div>
  );
}

