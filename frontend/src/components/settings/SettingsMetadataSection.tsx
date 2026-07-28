import { useEffect, useState } from 'react';
import { getProjectMetadata, saveProjectMetadata, type ProjectMetadata } from '../../api/client';
import { SettingsVisualBaselineContractSection } from './SettingsVisualBaselineContractSection';
import { SettingsVisualBaselineSection } from './SettingsVisualBaselineSection';

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
      setStatus('已保存。行业、业务模块范围与生产数据禁触边界已写入被测系统配置，将驱动后续执行。');
    } catch (err: unknown) {
      setStatus(`保存失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="section-card">
        <div className="settings-card-head">
          <div>
            <span className="panel-kicker">被测系统信息</span>
            <h2>行业与执行安全边界</h2>
          </div>
        </div>
        <div className="settings-card-note">
          填写被测系统的行业、业务模块范围，以及<strong>禁止触碰的生产数据边界</strong>。
          生产数据禁触范围内的接口/数据，执行引擎将硬性阻断，绝不触碰。每行一项，支持 <code>re:</code> 前缀写正则。
        </div>

        <div className="form-group">
          <label className="form-label">行业类型</label>
          <input
            className="form-input"
            value={industry}
            disabled={loading}
            placeholder="如：电商 / 金融 / 医疗 / 制造业 / SaaS"
            onChange={(e) => {
              setIndustry(e.target.value);
              markDirty();
            }}
          />
        </div>

        <div className="form-group">
          <label className="form-label">业务模块范围</label>
          <textarea
            className="form-input settings-textarea"
            value={moduleScope}
            disabled={loading}
            rows={3}
            placeholder={'每行一个模块，例如：\n订单管理\n库存管理\n账户中心'}
            onChange={(e) => {
              setModuleScope(e.target.value);
              markDirty();
            }}
          />
        </div>

        <div className="form-group">
          <label className="form-label">生产数据禁触范围</label>
          <textarea
            className="form-input settings-textarea"
            value={exclusion}
            disabled={loading}
            rows={4}
            placeholder={'每行一个禁触接口或数据路径，例如：\n/api/admin/users\n/api/finance/settle\nre:/api/.*/export$'}
            onChange={(e) => {
              setExclusion(e.target.value);
              markDirty();
            }}
          />
          <span className="settings-hint">
            命中任一模式的请求会被执行引擎硬性阻断，用于保护生产核心数据。
          </span>
        </div>

        <div className="settings-compact-row settings-mt-10">
          <button onClick={handleSave} className="btn btn-primary settings-btn-compact" disabled={saving || loading}>
            {saving ? '保存中…' : '保存配置'}
          </button>
          {dirty && !saving && <span className="settings-inline-feedback">有未保存修改</span>}
        </div>

        {status && <p className="settings-inline-feedback">{status}</p>}
      </div>

      <SettingsVisualBaselineSection project={project} />
      <SettingsVisualBaselineContractSection project={project} />
    </>
  );
}
