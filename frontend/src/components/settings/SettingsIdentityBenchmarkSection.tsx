import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from 'react';
import {
  getIdentityBenchmarkWorkspace,
  importIdentityGroundTruth,
  saveIdentityQualityPolicy,
  type IdentityBenchmarkWorkspace,
} from '../../api/identityBenchmark';

type Props = { project: string };
type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function numberText(value: unknown): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? String(parsed) : '';
}

function percent(value: unknown): string {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : '未测量';
}

function downloadJson(filename: string, payload: unknown): void {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function SettingsIdentityBenchmarkSection({ project }: Props) {
  const [workspace, setWorkspace] = useState<IdentityBenchmarkWorkspace | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('');
  const [enforce, setEnforce] = useState(false);
  const [precision, setPrecision] = useState('0.98');
  const [recall, setRecall] = useState('0.95');
  const [overmerge, setOvermerge] = useState('0.02');
  const [undermerge, setUndermerge] = useState('0.05');
  const [silentErrors, setSilentErrors] = useState('0');

  const refresh = useCallback(async () => {
    if (!project) {
      setWorkspace(null);
      return;
    }
    setLoading(true);
    setStatus('');
    try {
      const next = await getIdentityBenchmarkWorkspace(project);
      setWorkspace(next);
      const policy = record(next.quality_policy);
      const thresholds = record(policy.thresholds);
      setEnforce(Boolean(policy.enforce));
      if (thresholds.minimum_pairwise_precision !== undefined) setPrecision(numberText(thresholds.minimum_pairwise_precision));
      if (thresholds.minimum_pairwise_recall !== undefined) setRecall(numberText(thresholds.minimum_pairwise_recall));
      if (thresholds.maximum_overmerge_rate !== undefined) setOvermerge(numberText(thresholds.maximum_overmerge_rate));
      if (thresholds.maximum_undermerge_rate !== undefined) setUndermerge(numberText(thresholds.maximum_undermerge_rate));
      if (thresholds.maximum_silent_identity_error_count !== undefined) setSilentErrors(numberText(thresholds.maximum_silent_identity_error_count));
    } catch (error: unknown) {
      setWorkspace(null);
      setStatus(`加载失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const benchmark = record(workspace?.benchmark);
  const metrics = record(benchmark.metrics);
  const qualityGate = record(workspace?.identity_quality_gate);
  const groundTruth = record(workspace?.ground_truth_summary);
  const manifest = workspace?.manifest || {};
  const measurementStatus = text(benchmark.status) || 'NOT_MEASURED';
  const qualityStatus = text(qualityGate.status) || 'NOT_CONFIGURED';
  const groundTruthPresent = Boolean(groundTruth.present);
  const qualityEnforced = Boolean(qualityGate.enforced);
  const canExport = Boolean(manifest.manifest_id && Array.isArray(manifest.mentions));
  const metricCards = useMemo(() => [
    { label: '精确率', value: percent(metrics.pairwise_precision) },
    { label: '召回率', value: percent(metrics.pairwise_recall) },
    { label: '过度融合', value: percent(metrics.overmerge_rate) },
    { label: '漏融合', value: percent(metrics.undermerge_rate) },
  ], [metrics.pairwise_precision, metrics.pairwise_recall, metrics.overmerge_rate, metrics.undermerge_rate]);

  const handleDownload = () => {
    if (!canExport) {
      setStatus('当前没有可导出的身份 Mention 清单。');
      return;
    }
    downloadJson(`${project}-identity-annotation-manifest.json`, manifest);
    setStatus('已导出盲标清单。该文件不包含系统预测簇。');
  };

  const handleImport = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || !project || !manifest.manifest_id) return;
    setSaving(true);
    setStatus('正在校验并重建企业理解模型…');
    try {
      const parsed = JSON.parse(await file.text()) as JsonRecord;
      const next = await importIdentityGroundTruth(project, manifest.manifest_id, parsed);
      setWorkspace(next);
      setStatus('Ground Truth 已导入，身份指标和 Gate 已由后端重新计算。');
    } catch (error: unknown) {
      setStatus(`导入失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  const handlePolicySave = async () => {
    if (!project) return;
    setSaving(true);
    setStatus('正在保存质量策略并重建…');
    try {
      const next = await saveIdentityQualityPolicy(project, {
        schema: 'qualibug.enterprise-identity-quality-policy.v1',
        enforce,
        thresholds: {
          minimum_pairwise_precision: Number(precision),
          minimum_pairwise_recall: Number(recall),
          maximum_overmerge_rate: Number(overmerge),
          maximum_undermerge_rate: Number(undermerge),
          maximum_silent_identity_error_count: Number(silentErrors),
        },
      });
      setWorkspace(next);
      setStatus('质量策略已保存。页面只展示后端评测结果，不在浏览器重新评分。');
    } catch (error: unknown) {
      setStatus(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <details className="section-card settings-span-2">
      <summary>
        <strong>跨资料身份融合质量校准</strong>
        <span className="muted">盲标清单 · Ground Truth · Precision / Recall Gate</span>
      </summary>
      <div className="settings-card-note settings-mt-10">
        系统只导出源出现级 Mention，不导出预测实体或推荐分组。人工完成封闭世界标注后，后端统一计算精确率、召回率、过度融合和漏融合，并按策略决定是否阻断正式理解。
      </div>

      <div className="customer-summary-grid settings-mt-10">
        <article className="customer-summary-card tone-neutral">
          <span>标注 Mention</span>
          <strong>{Number(manifest.mention_count || 0)}</strong>
          <small>Manifest {text(manifest.manifest_id) ? '已生成' : '待生成'}</small>
        </article>
        <article className="customer-summary-card tone-neutral">
          <span>Ground Truth</span>
          <strong>{groundTruthPresent ? '已导入' : '未导入'}</strong>
          <small>{Number(groundTruth.annotated_mention_count || 0)} 个 Mention</small>
        </article>
        <article className="customer-summary-card tone-neutral">
          <span>测量状态</span>
          <strong>{measurementStatus}</strong>
          <small>无封闭世界标注时不得声明准确率</small>
        </article>
        <article className="customer-summary-card tone-neutral">
          <span>质量 Gate</span>
          <strong>{qualityStatus}</strong>
          <small>{qualityEnforced ? '已强制执行' : '仅报告'}</small>
        </article>
      </div>

      <div className="customer-summary-grid settings-mt-10">
        {metricCards.map((item) => (
          <article key={item.label} className="customer-summary-card tone-neutral">
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>后端 occurrence-level benchmark</small>
          </article>
        ))}
      </div>

      <div className="settings-compact-row settings-mt-10">
        <button className="btn btn-secondary settings-btn-compact" disabled={!project || loading || saving || !canExport} onClick={handleDownload}>
          导出盲标清单
        </button>
        <label className={`btn btn-secondary settings-btn-compact ${!project || saving || !manifest.manifest_id ? 'disabled' : ''}`}>
          导入 Ground Truth JSON
          <input
            type="file"
            accept="application/json,.json"
            hidden
            disabled={!project || saving || !manifest.manifest_id}
            onChange={handleImport}
          />
        </label>
        <button className="btn btn-secondary settings-btn-compact" disabled={!project || loading || saving} onClick={() => void refresh()}>
          刷新结果
        </button>
      </div>

      <details className="settings-auth-section settings-mt-10">
        <summary><strong>身份质量阈值</strong> <span className="muted">可选择仅报告或正式阻断</span></summary>
        <label className="settings-compact-row settings-mt-10">
          <input type="checkbox" checked={enforce} onChange={(event) => setEnforce(event.target.checked)} />
          <span>低于阈值时阻断正式企业理解与后续规划</span>
        </label>
        <div className="settings-grid settings-mt-10">
          <label className="form-group"><span className="form-label">最低精确率</span><input className="form-input" value={precision} onChange={(event) => setPrecision(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">最低召回率</span><input className="form-input" value={recall} onChange={(event) => setRecall(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">最大过度融合率</span><input className="form-input" value={overmerge} onChange={(event) => setOvermerge(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">最大漏融合率</span><input className="form-input" value={undermerge} onChange={(event) => setUndermerge(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">最大静默错误数</span><input className="form-input" value={silentErrors} onChange={(event) => setSilentErrors(event.target.value)} /></label>
        </div>
        <button className="btn btn-secondary settings-btn-compact settings-mt-10" disabled={!project || saving} onClick={handlePolicySave}>
          {saving ? '处理中…' : '保存身份质量策略'}
        </button>
      </details>

      {status && <p className="settings-inline-feedback">{status}</p>}
    </details>
  );
}
