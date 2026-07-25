import { useState } from 'react';
import type { Finding } from '../../types';
import { hasRealReplayAsset } from '../../api/data';

interface ReplayPanelProps {
  finding: Finding;
  project: string;
  onReplay: (finding: Finding) => void;
}

export function ReplayPanel({ finding, onReplay }: ReplayPanelProps) {
  const [copied, setCopied] = useState(false);
  const replayable = hasRealReplayAsset(finding);
  const command = finding.evidence_quality?.curl_command || finding.reproduction?.curl_command || '';
  const reproduction = finding.reproduction;

  const handleCopy = () => {
    void navigator.clipboard.writeText(command);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div style={{ marginTop: 16 }}>
      <h4 style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>请求回放</h4>
      {replayable && command ? (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: 12, color: 'var(--subtle)' }}>
              {reproduction?.method || 'GET'} {reproduction?.path || ''}
            </span>
            <button className="btn btn-secondary btn-sm" onClick={handleCopy}>{copied ? '已复制' : '复制'}</button>
          </div>
          <pre style={{ fontSize: 11.5, background: 'var(--ink)', color: '#e8eefc', padding: 14, borderRadius: 8, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 160 }}>
            <code>{command}</code>
          </pre>
          <button className="btn btn-primary btn-sm" style={{ marginTop: 8 }} onClick={() => onReplay(finding)}>
            点击复现
          </button>
        </>
      ) : (
        <p style={{ fontSize: 13, color: 'var(--subtle)' }}>当前未沉淀真实复验入口，无法回放。</p>
      )}
      {reproduction?.steps && reproduction.steps.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <strong style={{ fontSize: 12, color: 'var(--subtle)' }}>前端操作步骤</strong>
          <ol style={{ fontSize: 13, paddingLeft: 18, marginTop: 6 }}>
            {reproduction.steps.map((step, i) => <li key={i}>{step}</li>)}
          </ol>
        </div>
      )}
    </div>
  );
}
