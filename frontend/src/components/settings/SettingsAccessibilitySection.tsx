import { useMemo, useRef, useState } from 'react';
import {
  ACCESSIBILITY_RULE_PRESETS,
  DEFAULT_CUSTOM_ACCESSIBILITY_RULES,
  buildAccessibilityContractStep,
  type AccessibilityRuleId,
} from '../../lib/accessibility-contract';
import '../../styles/browser-matrix-settings.css';

export function SettingsAccessibilitySection() {
  const outputRef = useRef<HTMLTextAreaElement | null>(null);
  const [mode, setMode] = useState<'standard' | 'custom'>('standard');
  const [selectedRules, setSelectedRules] = useState<AccessibilityRuleId[]>(
    DEFAULT_CUSTOM_ACCESSIBILITY_RULES,
  );
  const [status, setStatus] = useState('');

  const json = useMemo(() => {
    try {
      return JSON.stringify(buildAccessibilityContractStep(mode, selectedRules), null, 2);
    } catch {
      return '';
    }
  }, [mode, selectedRules]);

  const toggleRule = (rule: AccessibilityRuleId) => {
    setStatus('');
    setSelectedRules((current) => current.includes(rule)
      ? current.filter((value) => value !== rule)
      : [...current, rule]);
  };

  const copy = async () => {
    if (!json) {
      setStatus('自定义合同至少需要选择一条规则。');
      return;
    }
    try {
      await navigator.clipboard.writeText(json);
      setStatus('已复制无障碍步骤，请放入 browser_plan.steps。');
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
          <span className="panel-kicker">UI 无障碍验证</span>
          <h2>确定性无障碍规则</h2>
          <p>生成来源声明的 WCAG 2.2 A/AA 确定性检查步骤，不使用 AI 主观判断。</p>
        </div>
        <strong className="is-positive">
          {mode === 'standard' ? '高置信标准' : `${selectedRules.length} 条规则`}
        </strong>
      </div>

      <div className="browser-matrix-policy">
        完整标准固定零缺陷预算、禁止排除区域和不可测试豁免；复杂背景、扫描截断或无法证明的规则进入
        INDETERMINATE，不会被当作通过。带明显业务例外的规则只能在自定义模式中由来源显式启用。
      </div>

      <div className="browser-matrix-profile-grid" role="radiogroup" aria-label="无障碍合同模式">
        <label className="browser-matrix-profile">
          <input
            type="radio"
            name="accessibility-contract-mode"
            checked={mode === 'standard'}
            onChange={() => {
              setMode('standard');
              setStatus('');
            }}
          />
          <span>
            <strong>完整高置信标准</strong>
            <small>执行默认确定性规则子集，不允许削弱标准身份。</small>
            <em>wcag22-aa-deterministic</em>
          </span>
        </label>
        <label className="browser-matrix-profile">
          <input
            type="radio"
            name="accessibility-contract-mode"
            checked={mode === 'custom'}
            onChange={() => {
              setMode('custom');
              setStatus('');
            }}
          />
          <span>
            <strong>来源自定义规则集</strong>
            <small>仅执行被测系统资料明确授权的规则。</small>
            <em>source-declared-rule-set</em>
          </span>
        </label>
      </div>

      {mode === 'custom' && (
        <div className="browser-matrix-profile-grid" style={{ marginTop: '0.75rem' }}>
          {ACCESSIBILITY_RULE_PRESETS.map((preset) => (
            <label key={preset.rule} className="browser-matrix-profile">
              <input
                type="checkbox"
                checked={selectedRules.includes(preset.rule)}
                onChange={() => toggleRule(preset.rule)}
              />
              <span>
                <strong>{preset.label}</strong>
                <small>{preset.note}</small>
                <em>{preset.wcag}{preset.customOnly ? ' · 仅自定义' : ''}</em>
              </span>
            </label>
          ))}
        </div>
      )}

      <div className="browser-matrix-output-head">
        <div>
          <h3>browser_plan 步骤</h3>
          <p>该片段必须由真实企业资料或直接扫描来源身份绑定后进入正式执行链。</p>
        </div>
        <button
          type="button"
          className="btn btn-secondary settings-btn-compact"
          disabled={!json}
          onClick={copy}
        >
          复制规则 JSON
        </button>
      </div>
      <textarea
        ref={outputRef}
        className="form-input settings-textarea browser-matrix-output"
        rows={12}
        readOnly
        spellCheck={false}
        value={json}
        aria-label="无障碍规则合同 JSON"
      />
      {status && <p className="settings-inline-feedback" role="status">{status}</p>}
    </div>
  );
}
