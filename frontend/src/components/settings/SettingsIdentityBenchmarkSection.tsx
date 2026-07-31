import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from 'react';
import {
  getIdentityBenchmarkWorkspace,
  importIdentityGroundTruth,
  runIdentityBenchmark,
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

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
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

function signedPercent(value: unknown): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '—';
  const sign = parsed > 0 ? '+' : '';
  return `${sign}${(parsed * 100).toFixed(2)}%`;
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

function errorPairLabel(value: unknown): string {
  const row = record(value);
  const left = record(row.left);
  const right = record(row.right);
  const leftLabel = text(left.label) || text(left.mention_ref) || '未知 Mention';
  const rightLabel = text(right.label) || text(right.mention_ref) || '未知 Mention';
  return `${leftLabel} ↔ ${rightLabel}`;
}

function errorPairSource(value: unknown): string {
  const row = record(value);
  const left = record(row.left);
  const right = record(row.right);
  const leftSource = [text(left.source_id), text(left.source_locator)].filter(Boolean).join(' / ');
  const rightSource = [text(right.source_id), text(right.source_locator)].filter(Boolean).join(' / ');
  return `${leftSource || '未知来源'} ｜ ${rightSource || '未知来源'}`;
}

export function SettingsIdentityBenchmarkSection({ project }: Props) {
  const [workspace, setWorkspace] = useState<IdentityBenchmarkWorkspace | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('');
  const [enforce, setEnforce] = useState(false);
  const [enforceRegression, setEnforceRegression] = useState(false);
  const [precision, setPrecision] = useState('0.98');
  const [recall, setRecall] = useState('0.95');
  const [overmerge, setOvermerge] = useState('0.02');
  const [undermerge, setUndermerge] = useState('0.05');
  const [silentErrors, setSilentErrors] = useState('0');
  const [precisionDrop, setPrecisionDrop] = useState('0.01');
  const [recallDrop, setRecallDrop] = useState('0.01');
  const [f1Drop, setF1Drop] = useState('0.01');
  const [overmergeIncrease, setOvermergeIncrease] = useState('0.01');
  const [undermergeIncrease, setUndermergeIncrease] = useState('0.01');
  const [silentErrorIncrease, setSilentErrorIncrease] = useState('0');

  const applyWorkspace = useCallback((next: IdentityBenchmarkWorkspace) => {
    setWorkspace(next);
    const policy = record(next.quality_policy);
    const thresholds = record(policy.thresholds);
    const regressionThresholds = record(policy.regression_thresholds);
    setEnforce(Boolean(policy.enforce));
    setEnforceRegression(Boolean(policy.enforce_regression));
    if (thresholds.minimum_pairwise_precision !== undefined) setPrecision(numberText(thresholds.minimum_pairwise_precision));
    if (thresholds.minimum_pairwise_recall !== undefined) setRecall(numberText(thresholds.minimum_pairwise_recall));
    if (thresholds.maximum_overmerge_rate !== undefined) setOvermerge(numberText(thresholds.maximum_overmerge_rate));
    if (thresholds.maximum_undermerge_rate !== undefined) setUndermerge(numberText(thresholds.maximum_undermerge_rate));
    if (thresholds.maximum_silent_identity_error_count !== undefined) setSilentErrors(numberText(thresholds.maximum_silent_identity_error_count));
    if (regressionThresholds.maximum_pairwise_precision_drop !== undefined) setPrecisionDrop(numberText(regressionThresholds.maximum_pairwise_precision_drop));
    if (regressionThresholds.maximum_pairwise_recall_drop !== undefined) setRecallDrop(numberText(regressionThresholds.maximum_pairwise_recall_drop));
    if (regressionThresholds.maximum_pairwise_f1_drop !== undefined) setF1Drop(numberText(regressionThresholds.maximum_pairwise_f1_drop));
    if (regressionThresholds.maximum_overmerge_rate_increase !== undefined) setOvermergeIncrease(numberText(regressionThresholds.maximum_overmerge_rate_increase));
    if (regressionThresholds.maximum_undermerge_rate_increase !== undefined) setUndermergeIncrease(numberText(regressionThresholds.maximum_undermerge_rate_increase));
    if (regressionThresholds.maximum_silent_identity_error_increase !== undefined) setSilentErrorIncrease(numberText(regressionThresholds.maximum_silent_identity_error_increase));
  }, []);

  const refresh = useCallback(async () => {
    if (!project) {
      setWorkspace(null);
      return;
    }
    setLoading(true);
    setStatus('');
    try {
      applyWorkspace(await getIdentityBenchmarkWorkspace(project));
    } catch (error: unknown) {
      setWorkspace(null);
      setStatus(`加载失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setLoading(false);
    }
  }, [applyWorkspace, project]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const benchmark = record(workspace?.benchmark);
  const metrics = record(benchmark.metrics);
  const regression = record(workspace?.regression || benchmark.regression);
  const regressionDeltas = record(regression.metric_deltas);
  const qualityGate = record(workspace?.identity_quality_gate);
  const groundTruth = record(workspace?.ground_truth_summary);
  const history = record(workspace?.history);
  const latestSnapshot = record(history.latest_snapshot);
  const errorQueue = record(workspace?.error_queue || history.error_queue);
  const activeErrors = array(errorQueue.active_errors).map(record);
  const resolvedErrors = array(errorQueue.resolved_errors).map(record);
  const manifest = workspace?.manifest || {};
  const measurementStatus = text(benchmark.status) || 'NOT_MEASURED';
  const qualityStatus = text(qualityGate.status) || 'NOT_CONFIGURED';
  const regressionStatus = text(regression.status) || 'NOT_COMPARABLE';
  const groundTruthPresent = Boolean(groundTruth.present);
  const qualityEnforced = Boolean(qualityGate.enforced);
  const canExport = Boolean(manifest.manifest_id && Array.isArray(manifest.mentions));
  const metricCards = useMemo(() => [
    { label: '精确率', value: percent(metrics.pairwise_precision), delta: signedPercent(regressionDeltas.pairwise_precision) },
    { label: '召回率', value: percent(metrics.pairwise_recall), delta: signedPercent(regressionDeltas.pairwise_recall) },
    { label: '过度融合', value: percent(metrics.overmerge_rate), delta: signedPercent(regressionDeltas.overmerge_rate) },
    { label: '漏融合', value: percent(metrics.undermerge_rate), delta: signedPercent(regressionDeltas.undermerge_rate) },
  ], [
    metrics.pairwise_precision,
    metrics.pairwise_recall,
    metrics.overmerge_rate,
    metrics.undermerge_rate,
    regressionDeltas.pairwise_precision,
    regressionDeltas.pairwise_recall,
    regressionDeltas.overmerge_rate,
    regressionDeltas.undermerge_rate,
  ]);

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
    setStatus('正在校验、重建并记录基线快照…');
    try {
      const parsed = JSON.parse(await file.text()) as JsonRecord;
      applyWorkspace(await importIdentityGroundTruth(project, manifest.manifest_id, parsed));
      setStatus('Ground Truth 已导入，身份指标、错误队列和首个快照已由后端生成。');
    } catch (error: unknown) {
      setStatus(`导入失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleRun = async () => {
    if (!project) return;
    setSaving(true);
    setStatus('正在通过企业理解主链重新评测并记录快照…');
    try {
      applyWorkspace(await runIdentityBenchmark(project));
      setStatus('重新评测完成。只有同一 Manifest 与 Ground Truth 的结果参与回归比较。');
    } catch (error: unknown) {
      setStatus(`评测失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  const handlePolicySave = async () => {
    if (!project) return;
    setSaving(true);
    setStatus('正在保存静态与回归质量策略并重建…');
    try {
      applyWorkspace(await saveIdentityQualityPolicy(project, {
        schema: 'qualibug.enterprise-identity-quality-policy.v1',
        enforce,
        enforce_regression: enforceRegression,
        thresholds: {
          minimum_pairwise_precision: Number(precision),
          minimum_pairwise_recall: Number(recall),
          maximum_overmerge_rate: Number(overmerge),
          maximum_undermerge_rate: Number(undermerge),
          maximum_silent_identity_error_count: Number(silentErrors),
        },
        regression_thresholds: {
          maximum_pairwise_precision_drop: Number(precisionDrop),
          maximum_pairwise_recall_drop: Number(recallDrop),
          maximum_pairwise_f1_drop: Number(f1Drop),
          maximum_overmerge_rate_increase: Number(overmergeIncrease),
          maximum_undermerge_rate_increase: Number(undermergeIncrease),
          maximum_silent_identity_error_increase: Number(silentErrorIncrease),
        },
      }));
      setStatus('质量策略已保存。静态阈值与可比快照回归共同形成后端身份质量 Gate。');
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
        <span className="muted">盲标 · 快照 · 回归 · 错误证据队列</span>
      </summary>
      <div className="settings-card-note settings-mt-10">
        系统只导出源出现级 Mention，不导出预测实体或推荐分组。人工完成封闭世界标注后，后端统一计算指标并保存版本快照；只有 Manifest ID 和外部 Ground Truth 指纹完全一致的快照才允许比较回归。
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
          <span>测量 / 回归</span>
          <strong>{measurementStatus}</strong>
          <small>{regressionStatus}</small>
        </article>
        <article className="customer-summary-card tone-neutral">
          <span>质量 Gate</span>
          <strong>{qualityStatus}</strong>
          <small>{qualityEnforced ? '已强制执行' : '仅报告'}</small>
        </article>
        <article className="customer-summary-card tone-neutral">
          <span>历史快照</span>
          <strong>{Number(history.snapshot_count || 0)}</strong>
          <small>{text(latestSnapshot.recorded_at_utc) || '尚未记录'}</small>
        </article>
      </div>

      <div className="customer-summary-grid settings-mt-10">
        {metricCards.map((item) => (
          <article key={item.label} className="customer-summary-card tone-neutral">
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>相对可比基线 {item.delta}</small>
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
        <button className="btn btn-secondary settings-btn-compact" disabled={!project || loading || saving || !groundTruthPresent} onClick={handleRun}>
          重新评测并记录快照
        </button>
        <button className="btn btn-secondary settings-btn-compact" disabled={!project || loading || saving} onClick={() => void refresh()}>
          刷新结果
        </button>
      </div>

      <details className="settings-auth-section settings-mt-10">
        <summary><strong>身份质量阈值</strong> <span className="muted">静态质量与版本回归使用同一个最终 Gate</span></summary>
        <label className="settings-compact-row settings-mt-10">
          <input type="checkbox" checked={enforce} onChange={(event: ChangeEvent<HTMLInputElement>) => setEnforce(event.target.checked)} />
          <span>低于绝对质量阈值时阻断正式企业理解</span>
        </label>
        <label className="settings-compact-row settings-mt-10">
          <input type="checkbox" checked={enforceRegression} onChange={(event: ChangeEvent<HTMLInputElement>) => setEnforceRegression(event.target.checked)} />
          <span>相对同一标注基线发生超限退化时阻断正式企业理解</span>
        </label>

        <div className="settings-card-note settings-mt-10">绝对质量阈值</div>
        <div className="settings-grid settings-mt-10">
          <label className="form-group"><span className="form-label">最低精确率</span><input className="form-input" value={precision} onChange={(event: ChangeEvent<HTMLInputElement>) => setPrecision(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">最低召回率</span><input className="form-input" value={recall} onChange={(event: ChangeEvent<HTMLInputElement>) => setRecall(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">最大过度融合率</span><input className="form-input" value={overmerge} onChange={(event: ChangeEvent<HTMLInputElement>) => setOvermerge(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">最大漏融合率</span><input className="form-input" value={undermerge} onChange={(event: ChangeEvent<HTMLInputElement>) => setUndermerge(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">最大静默错误数</span><input className="form-input" value={silentErrors} onChange={(event: ChangeEvent<HTMLInputElement>) => setSilentErrors(event.target.value)} /></label>
        </div>

        <div className="settings-card-note settings-mt-10">相对可比基线的最大允许退化</div>
        <div className="settings-grid settings-mt-10">
          <label className="form-group"><span className="form-label">精确率最大下降</span><input className="form-input" value={precisionDrop} onChange={(event: ChangeEvent<HTMLInputElement>) => setPrecisionDrop(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">召回率最大下降</span><input className="form-input" value={recallDrop} onChange={(event: ChangeEvent<HTMLInputElement>) => setRecallDrop(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">F1 最大下降</span><input className="form-input" value={f1Drop} onChange={(event: ChangeEvent<HTMLInputElement>) => setF1Drop(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">过度融合最大上升</span><input className="form-input" value={overmergeIncrease} onChange={(event: ChangeEvent<HTMLInputElement>) => setOvermergeIncrease(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">漏融合最大上升</span><input className="form-input" value={undermergeIncrease} onChange={(event: ChangeEvent<HTMLInputElement>) => setUndermergeIncrease(event.target.value)} /></label>
          <label className="form-group"><span className="form-label">静默错误最大增加</span><input className="form-input" value={silentErrorIncrease} onChange={(event: ChangeEvent<HTMLInputElement>) => setSilentErrorIncrease(event.target.value)} /></label>
        </div>
        <button className="btn btn-secondary settings-btn-compact settings-mt-10" disabled={!project || saving} onClick={handlePolicySave}>
          {saving ? '处理中…' : '保存身份质量策略'}
        </button>
      </details>

      <details className="settings-auth-section settings-mt-10">
        <summary>
          <strong>身份错误证据队列</strong>
          <span className="muted">当前 {activeErrors.length} · 已解决 {resolvedErrors.length}</span>
        </summary>
        <div className="settings-card-note settings-mt-10">
          这里只展示外部 Ground Truth 与系统结果之间的精确 Mention 对差异，不使用名称相似度或大模型猜测根因。
        </div>
        {activeErrors.length === 0 ? (
          <p className="settings-inline-feedback">当前没有可测量的过度融合或漏融合错误。</p>
        ) : activeErrors.slice(0, 20).map((row) => (
          <div className="settings-card-note settings-mt-10" key={text(row.error_id)}>
            <strong>{text(row.error_type) === 'OVERMERGE_FALSE_POSITIVE_PAIR' ? '过度融合' : '漏融合'} · {text(row.lifecycle_status)}</strong>
            <div>{errorPairLabel(row)}</div>
            <small>{errorPairSource(row)}</small>
          </div>
        ))}
      </details>

      {status && <p className="settings-inline-feedback">{status}</p>}
    </details>
  );
}
