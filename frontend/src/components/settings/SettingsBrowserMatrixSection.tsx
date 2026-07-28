import { useMemo, useRef, useState } from 'react';
import {
  BROWSER_MATRIX_PRESETS,
  buildBrowserMatrixContract,
} from '../../lib/browser-matrix-contract';
import '../../styles/browser-matrix-settings.css';

export function SettingsBrowserMatrixSection() {
  const outputRef = useRef<HTMLTextAreaElement | null>(null);
  const [selected, setSelected] = useState(() => BROWSER_MATRIX_PRESETS
    .filter((profile) => profile.enabledByDefault)
    .map((profile) => profile.profile_id));
  const [status, setStatus] = useState('');

  const valid = selected.length >= 2 && selected.length <= 12;
  const json = useMemo(() => {
    try {
      return JSON.stringify(buildBrowserMatrixContract(selected), null, 2);
    } catch {
      return '';
    }
  }, [selected]);

  const toggle = (profileId: string) => {
    setStatus('');
    setSelected((current) => current.includes(profileId)
      ? current.filter((value) => value !== profileId)
      : [...current, profileId]);
  };

  const copy = async () => {
    if (!valid || !json) {
      setStatus('矩阵至少需要 2 个唯一 profile。');
      return;
    }
    try {
      await navigator.clipboard.writeText(json);
      setStatus('已复制 browser_matrix，请放入正式 ui_request。');
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
          <span className="panel-kicker">UI 多环境验证</span>
          <h2>浏览器与设备矩阵</h2>
          <p>生成来源声明的 Chromium、Firefox、WebKit 只读执行矩阵。</p>
        </div>
        <strong className={valid ? 'is-positive' : 'is-neutral'}>{selected.length} 个 profile</strong>
      </div>

      <div className="browser-matrix-policy">
        全部 profile 完成才可证明通过；typed assertion failure 可证明对应环境缺陷；
        浏览器启动异常只进入 INDETERMINATE。跨引擎视觉基线和写操作暂不扩展。
      </div>

      <div className="browser-matrix-profile-grid">
        {BROWSER_MATRIX_PRESETS.map((profile) => (
          <label key={profile.profile_id} className="browser-matrix-profile">
            <input
              type="checkbox"
              checked={selected.includes(profile.profile_id)}
              onChange={() => toggle(profile.profile_id)}
            />
            <span>
              <strong>{profile.label}</strong>
              <small>{profile.note}</small>
              <em>
                {profile.browser_engine} · {profile.viewport_width} × {profile.viewport_height}
                {profile.has_touch ? ' · Touch' : ''}
              </em>
            </span>
          </label>
        ))}
      </div>

      <div className="browser-matrix-output-head">
        <div>
          <h3>browser_matrix 合同片段</h3>
          <p>引擎、设备、视口、DPR、Locale、时区和媒体偏好均为正式身份。</p>
        </div>
        <button type="button" className="btn btn-secondary settings-btn-compact" disabled={!valid} onClick={copy}>
          复制矩阵 JSON
        </button>
      </div>
      <textarea
        ref={outputRef}
        className="form-input settings-textarea browser-matrix-output"
        rows={11}
        readOnly
        spellCheck={false}
        value={json}
        aria-label="浏览器与设备矩阵 JSON"
      />
      {status && <p className="settings-inline-feedback">{status}</p>}
    </div>
  );
}
