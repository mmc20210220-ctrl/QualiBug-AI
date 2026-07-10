import { useCallback, useState } from 'react';
import { replayFinding } from '../api/client';
import { formatDurationMs } from '../lib/display';
import type { Finding, ReplayResult } from '../types';

interface ReplayViewerProps {
  projectId: string;
  finding: Finding;
  onClose: () => void;
}

function asReplayResult(value: unknown): ReplayResult | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as ReplayResult;
}

export function ReplayViewer({ projectId, finding, onClose }: ReplayViewerProps) {
  const [result, setResult] = useState<ReplayResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleReplay = useCallback(async () => {
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const replay = asReplayResult(await replayFinding(projectId, finding.id));
      if (!replay) throw new Error('复现接口返回了无法识别的数据');
      setResult(replay);
      if (!replay.ok && replay.error) setError(replay.error);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : '复现失败');
    } finally {
      setLoading(false);
    }
  }, [projectId, finding.id]);

  const request = result?.request;
  const response = result?.response;
  const diff = result?.diff;
  const originalEvidence = result?.original_evidence;
  const success = result?.success;

  return (
    <div className="replay-viewer-overlay" onClick={onClose}>
      <div className="replay-viewer" onClick={(event) => event.stopPropagation()}>
        <div className="replay-viewer-head">
          <div>
            <span className="panel-kicker">实时复现</span>
            <h3>{finding.title}</h3>
          </div>
          <button className="btn btn-secondary replay-close" onClick={onClose}>关闭</button>
        </div>

        <div className="replay-action-bar">
          <button className="btn btn-primary" onClick={handleReplay} disabled={loading}>
            {loading ? '复现中...' : '点击复现'}
          </button>
          {!loading && success === true && <span className="replay-status success">✓ 成功复现</span>}
          {!loading && success === false && <span className="replay-status warning">⚠ 未复现</span>}
          {error && <span className="replay-status danger">✕ {error}</span>}
        </div>

        {loading && (
          <div className="replay-loading">
            <div className="spinner spinner-centered" />
            <p>正在调用被测系统接口...</p>
          </div>
        )}

        {request && response && (
          <div className="replay-result">
            <div className="replay-timeline">
              <div className="replay-timeline-node">
                <span className="replay-timeline-dot request">●</span>
                <div><strong>请求</strong><p>{request.method} {request.url}</p><small>{request.timestamp}</small></div>
              </div>
              <div className="replay-timeline-line" />
              <div className="replay-timeline-node">
                <span className={`replay-timeline-dot response ${response.status_code >= 400 ? 'danger' : 'success'}`}>●</span>
                <div><strong>响应</strong><p>HTTP {response.status_code}</p><small>耗时 {formatDurationMs(response.duration_ms)}</small></div>
              </div>
            </div>

            <div className="replay-section">
              <div className="replay-section-head">请求详情</div>
              <div className="replay-code-block">
                <div className="replay-code-line"><span className="replay-code-key">Method:</span> {request.method}</div>
                <div className="replay-code-line"><span className="replay-code-key">URL:</span> {request.url}</div>
                {Object.entries(request.headers || {}).map(([key, value]) => <div key={key} className="replay-code-line"><span className="replay-code-key">{key}:</span> {value}</div>)}
                {request.body && <div className="replay-code-line"><span className="replay-code-key">Body:</span><code className="replay-response-body">{request.body}</code></div>}
              </div>
            </div>

            <div className="replay-section">
              <div className="replay-section-head">响应详情</div>
              <div className="replay-code-block">
                <div className="replay-code-line"><span className="replay-code-key">Status:</span><span className={response.status_code >= 400 ? 'tone-danger' : 'tone-success'}> {response.status_code}</span></div>
                <div className="replay-code-line"><span className="replay-code-key">耗时:</span> {formatDurationMs(response.duration_ms)}</div>
                {response.body && <div className="replay-code-line"><span className="replay-code-key">Body:</span><code className="replay-response-body">{response.body}</code></div>}
              </div>
            </div>

            {originalEvidence && diff && (
              <div className="replay-section">
                <div className="replay-section-head">证据对比（原始扫描 vs 实时复现）</div>
                <div className="replay-diff-grid">
                  <div className="replay-diff-card"><div className="replay-diff-label">原始扫描证据</div><div className="replay-diff-body"><div>状态码: {originalEvidence.status_code || '未记录'}</div><div className="replay-diff-excerpt">{originalEvidence.response_body_excerpt || '无响应体记录'}</div></div></div>
                  <div className="replay-diff-card"><div className="replay-diff-label">实时复现结果</div><div className="replay-diff-body"><div>状态码: {response.status_code}</div><div className="replay-diff-excerpt">{response.body.slice(0, 500) || '无响应体'}</div></div></div>
                </div>
                <div className="replay-diff-summary">
                  {diff.status_match ? '✓ 状态码一致' : '✕ 状态码不一致'} · {diff.body_match ? '✓ 响应体一致' : '✕ 响应体存在差异'}
                  {diff.key_differences.length > 0 && <ul className="replay-diff-differences">{diff.key_differences.map((difference, index) => <li key={`${difference}-${index}`}>{difference}</li>)}</ul>}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default ReplayViewer;
