import { useEffect, useState } from 'react';
import { getProjectMetadata, saveProjectMetadata, type ProjectMetadata } from '../../api/client';
import { SettingsOnboardingGuide } from './SettingsOnboardingGuide';

type SettingsMetadataSectionProps = {
  project: string;
};

function toLines(value: unknown): string {
  if (Array.isArray(value)) return value.join('\n');
  if (typeof value === 'string') return value;
  return '';
}

function toList(text: string): string[] {
  return text
    .split('\n')
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

export function SettingsMetadataSection({ project }: SettingsMetadataSectionProps) {
  const [industry, setIndustry] = useState('');
  const [moduleScope, setModuleScope] = useState('');
  const [exclusion, setExclusion] = useState('');
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!project) {
      setIndustry('');
      setModuleScope('');
      setExclusion('');
      setStatus('');
      setDirty(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setStatus('');
    getProjectMetadata(project)
      .then((meta: ProjectMetadata) => {
        if (cancelled) return;
        setIndustry(typeof meta.industry === 'string' ? meta.industry : '');
        setModuleScope(toLines(meta.module_scope));
        setExclusion(toLines(meta.production_data_exclusion));
        setDirty(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus(`加载失败：${err instanceof Error ? err.message : String(err)}`);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [project]);

  function markDirty() {
    setDirty(true);
    if (status && status.startsWith('已保存')) setStatus('');
  }

  async function handleSave() {
    if (!project) {
      setStatus('请先选择被测客户');
      return;
    }
    setSaving(true);
    setStatus('');
    try {
      await saveProjectMetadata({
        project,
        industry: industry.trim(),
        module_scope: toList(moduleScope),
        production_data_exclusion: toList(exclusion),
      });
      setDirty(false);
      setStatus('已保存。本次人工覆盖将作为后台自动理解与安全门禁的补充信息。');
    } catch (err: unknown) {
      setStatus(`保存失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <SettingsOnboardingGuide project={project} />
      <details className="section-card settings-span-2">
        <summary>
          <strong>异常覆盖：业务范围与绝对禁触边界</strong>
          <span className="muted">后台无法正确推断时再补充</span>
        </summary>
        <div className="settings-card-note settings-mt-10">
          行业、业务模块和页面视觉基线应由系统从企业资料、接口、页面结构和真实轨迹自动识别。
          这里只用于纠正明显识别错误，或声明任何情况下都禁止触碰的绝对安全边界。
        </div>

        <div className="form-group">
          <label className="form-label">行业覆盖（可选）</label>
          <input
            className="form-input"
            value={industry}
            disabled={loading}
            placeholder="留空则由后台自动判断"
            onChange={(e) => {
              setIndustry(e.target.value);
              markDirty();
            }}
          />
        </div>

        <div className="form-group">
          <label className="form-label">业务模块范围覆盖（可选）</label>
          <textarea
            className="form-input settings-textarea"
            value={moduleScope}
            disabled={loading}
            rows={3}
            placeholder={'留空则后台从资料和系统行为自动构建\n只有自动范围明显错误时才逐行补充'}
            onChange={(e) => {
              setModuleScope(e.target.value);
              markDirty();
            }}
          />
        </div>

        <div className="form-group">
          <label className="form-label">绝对禁触边界（可选）</label>
          <textarea
            className="form-input settings-textarea"
            value={exclusion}
            disabled={loading}
            rows={4}
            placeholder={'仅填写必须硬性禁止的接口或数据路径，例如：\n/api/finance/settle\nre:/api/.*/production-export$'}
            onChange={(e) => {
              setExclusion(e.target.value);
              markDirty();
            }}
          />
          <span className="settings-hint">
            这是安全熔断补充项，不是要求客户维护完整接口清单。命中任一模式的请求会被执行引擎硬性阻断。
          </span>
        </div>

        <div className="settings-compact-row settings-mt-10">
          <button onClick={handleSave} className="btn btn-secondary settings-btn-compact" disabled={saving || loading}>
            {saving ? '保存中…' : '保存异常覆盖'}
          </button>
          {dirty && !saving && <span className="settings-inline-feedback">有未保存修改</span>}
        </div>

        {status && <p className="settings-inline-feedback">{status}</p>}

      </details>
    </>
  );
}
