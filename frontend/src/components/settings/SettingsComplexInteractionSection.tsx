import { useMemo, useRef, useState } from 'react';
import {
  buildComplexInteractionStep,
  buildUploadCleanupStep,
  type ComplexInteractionKind,
} from '../../lib/complex-interaction-contract';
import '../../styles/browser-matrix-settings.css';

const OPTIONS: Array<{ value: ComplexInteractionKind; label: string; note: string }> = [
  { value: 'upload', label: '文件上传', note: '只引用上方治理面板审批生成的 uifb_ binding_ref，不接受文件路径或 base64。' },
  { value: 'download', label: '下载观察', note: '只保存 SHA-256、大小和文件名指纹，观察后删除下载文件。' },
  { value: 'popup', label: '新窗口 / 弹窗', note: '等待来源声明的最终 URL，观察完成后立即关闭。' },
  { value: 'iframe-click', label: 'iframe 内交互', note: '要求唯一 iframe selector 和精确批准 origin。' },
];

export function SettingsComplexInteractionSection() {
  const outputRef = useRef<HTMLTextAreaElement | null>(null);
  const [kind, setKind] = useState<ComplexInteractionKind>('upload');
  const [selector, setSelector] = useState('input[type=file]');
  const [fileRef, setFileRef] = useState('uifb_从上方审批面板复制');
  const [expectedUrl, setExpectedUrl] = useState('/export');
  const [expectedSha256, setExpectedSha256] = useState('');
  const [frameSelector, setFrameSelector] = useState('');
  const [frameOrigin, setFrameOrigin] = useState('');
  const [status, setStatus] = useState('');

  const output = useMemo(() => {
    try {
      const treatment = buildComplexInteractionStep({
        kind,
        selector,
        fileRef,
        expectedUrl,
        expectedSha256,
        frameSelector,
        frameOrigin,
      });
      const payload: Record<string, unknown> = { treatment_step: treatment };
      if (kind === 'upload') {
        payload.cleanup_step = buildUploadCleanupStep(
          selector,
          frameSelector,
          frameOrigin,
        );
      }
      return { json: JSON.stringify(payload, null, 2), error: '' };
    } catch (error) {
      return {
        json: '',
        error: error instanceof Error ? error.message : '复杂交互合同无效。',
      };
    }
  }, [expectedSha256, expectedUrl, fileRef, frameOrigin, frameSelector, kind, selector]);

  const copy = async () => {
    if (!output.json) {
      setStatus(output.error);
      return;
    }
    try {
      await navigator.clipboard.writeText(output.json);
      setStatus('已复制复杂交互步骤。还需补充 assertion、persistent probe 和业务 cleanup。');
    } catch {
      outputRef.current?.focus();
      outputRef.current?.select();
      setStatus('浏览器未授权自动复制，已选中 JSON。');
    }
  };

  return (
    <div className="section-card browser-matrix-section">
      <div className="settings-card-head browser-matrix-head">
        <div>
          <span className="panel-kicker">UI 复杂交互</span>
          <h2>上传、下载、弹窗与 iframe</h2>
          <p>生成受控写模式下的复杂交互步骤，继续服从 persistent cleanup 等价门禁。</p>
        </div>
        <strong className="is-positive">approved sandbox only</strong>
      </div>

      <div className="browser-matrix-policy">
        下载与弹窗不一致在 v1 只会使执行失败并进入 INDETERMINATE，暂不直接形成客户 Bug；文件上传必须使用上方治理面板生成的活动审批 binding_ref，扫描启动时会重新校验 registry、路径、大小和 SHA-256。
      </div>

      <div className="browser-matrix-profile-grid" role="radiogroup" aria-label="复杂交互类型">
        {OPTIONS.map((option) => (
          <label key={option.value} className="browser-matrix-profile">
            <input
              type="radio"
              name="complex-interaction-kind"
              checked={kind === option.value}
              onChange={() => {
                setKind(option.value);
                setStatus('');
              }}
            />
            <span>
              <strong>{option.label}</strong>
              <small>{option.note}</small>
              <em>{option.value}</em>
            </span>
          </label>
        ))}
      </div>

      <div className="settings-form-grid" style={{ marginTop: '0.8rem' }}>
        <label className="form-field">
          <span>目标 selector</span>
          <input className="form-input" value={selector} onChange={(event) => setSelector(event.target.value)} />
        </label>
        {kind === 'upload' && (
          <label className="form-field">
            <span>审批 binding_ref</span>
            <input className="form-input" value={fileRef} onChange={(event) => setFileRef(event.target.value)} />
          </label>
        )}
        {kind === 'download' && (
          <label className="form-field">
            <span>期望 SHA-256（可选）</span>
            <input className="form-input" value={expectedSha256} onChange={(event) => setExpectedSha256(event.target.value)} />
          </label>
        )}
        {kind === 'popup' && (
          <label className="form-field">
            <span>最终 URL / 路径</span>
            <input className="form-input" value={expectedUrl} onChange={(event) => setExpectedUrl(event.target.value)} />
          </label>
        )}
        <label className="form-field">
          <span>iframe selector（可选）</span>
          <input className="form-input" value={frameSelector} onChange={(event) => setFrameSelector(event.target.value)} />
        </label>
        <label className="form-field">
          <span>iframe 精确 origin（与 selector 同时填写）</span>
          <input
            className="form-input"
            placeholder="https://example.com"
            value={frameOrigin}
            onChange={(event) => setFrameOrigin(event.target.value)}
          />
        </label>
      </div>

      <div className="browser-matrix-output-head">
        <div>
          <h3>交互步骤片段</h3>
          <p>不能单独运行；正式计划还必须包含来源身份、断言、cleanup 合同和持久状态探针。</p>
        </div>
        <button
          type="button"
          className="btn btn-secondary settings-btn-compact"
          disabled={!output.json}
          onClick={copy}
        >
          复制步骤 JSON
        </button>
      </div>
      <textarea
        ref={outputRef}
        className="form-input settings-textarea browser-matrix-output"
        rows={16}
        readOnly
        spellCheck={false}
        value={output.json || output.error}
        aria-label="复杂 UI 交互步骤 JSON"
      />
      {status && <p className="settings-inline-feedback" role="status">{status}</p>}
    </div>
  );
}
