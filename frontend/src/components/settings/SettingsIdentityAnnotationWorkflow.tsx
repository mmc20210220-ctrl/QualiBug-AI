import { useState, type ChangeEvent } from 'react';
import {
  compileIdentityAnnotationSubmissions,
  getIdentityAnnotationTaskPackage,
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

export function SettingsIdentityAnnotationWorkflow({ project }: Props) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [review, setReview] = useState<JsonRecord | null>(null);

  async function handleDownloadPackage() {
    if (!project) {
      setStatus('请先选择客户。');
      return;
    }
    setBusy(true);
    setStatus('正在生成不含系统预测的标注任务包…');
    try {
      const taskPackage = await getIdentityAnnotationTaskPackage(project);
      downloadJson(`${project}-identity-annotation-tasks.json`, taskPackage);
      setStatus(`已导出 ${Number(taskPackage.task_count || 0)} 个标注任务，共 ${Number(taskPackage.batch_count || 0)} 个批次。`);
    } catch (error: unknown) {
      setStatus(`导出失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleCompile(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!project || files.length === 0) return;
    if (files.length > 3) {
      setStatus('最多选择三个文件：标注员 A、标注员 B、裁决员。');
      return;
    }
    setBusy(true);
    setReview(null);
    setStatus('正在校验封闭世界完整性并比较标注分区…');
    try {
      const parsed = await Promise.all(
        files.map(async (file) => JSON.parse(await file.text()) as JsonRecord),
      );
      const submissions: {
        primary_submission: JsonRecord;
        secondary_submission?: JsonRecord;
        adjudication_submission?: JsonRecord;
      } = { primary_submission: parsed[0] };
      if (parsed[1]) submissions.secondary_submission = parsed[1];
      if (parsed[2]) submissions.adjudication_submission = parsed[2];
      const result = await compileIdentityAnnotationSubmissions(project, submissions);
      const compilation = record(result.compilation);
      if (result.status === 'REVIEW_REQUIRED') {
        setReview(compilation);
        setStatus(`双人标注存在 ${Number(compilation.disagreement_count || 0)} 组分区分歧，Ground Truth 未导入。完成裁决后同时上传三个文件。`);
        return;
      }
      const benchmark = record(result.workspace?.benchmark);
      const metrics = record(benchmark.metrics);
      setStatus(
        `标注已编译并导入。复核状态 ${text(compilation.review_status) || 'READY'}，` +
        `Precision ${Number(metrics.pairwise_precision || 0).toFixed(4)}，` +
        `Recall ${Number(metrics.pairwise_recall || 0).toFixed(4)}。`,
      );
    } catch (error: unknown) {
      setStatus(`处理失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  const disagreements = array(review?.disagreements).map(record);

  return (
    <details className="section-card settings-span-2">
      <summary>
        <strong>人工盲标任务</strong>
        <span className="muted">源上下文 · 单人或双人盲标 · 自动编译 Ground Truth</span>
      </summary>
      <div className="settings-card-note settings-mt-10">
        任务包只包含准确 Mention、来源位置和受限上下文，不包含预测实体、推荐分组或相似度候选。
        每个 Mention 必须归入一个确认簇，单例也必须显式标注。双人标注比较的是成员分区，因此两人可以使用完全不同的簇名称。
      </div>
      <div className="settings-card-note settings-mt-10">
        下载后分别复制 submission_template 给标注员。上传一个文件按单人模式编译；上传两个文件按选择顺序作为标注员 A、B；存在分歧时，再把完整裁决提交作为第三个文件一并上传。
      </div>
      <div className="settings-compact-row settings-mt-10">
        <button
          className="btn btn-secondary settings-btn-compact"
          disabled={!project || busy}
          onClick={handleDownloadPackage}
        >
          导出标注任务包
        </button>
        <label className={`btn btn-secondary settings-btn-compact ${!project || busy ? 'disabled' : ''}`}>
          编译并导入标注结果
          <input
            type="file"
            accept="application/json,.json"
            multiple
            hidden
            disabled={!project || busy}
            onChange={handleCompile}
          />
        </label>
        {review && (
          <button
            className="btn btn-secondary settings-btn-compact"
            onClick={() => downloadJson(`${project}-identity-annotation-review.json`, review)}
          >
            导出分歧报告
          </button>
        )}
      </div>
      {disagreements.slice(0, 10).map((row) => (
        <div className="settings-card-note settings-mt-10" key={text(row.disagreement_id)}>
          <strong>需要裁决 · {array(row.affected_mention_refs).length} 个受影响 Mention</strong>
          <div>标注员 A：{array(row.primary_cluster_member_refs).map(String).join('、')}</div>
          <div>标注员 B：{array(row.secondary_cluster_member_refs).map(String).join('、')}</div>
        </div>
      ))}
      {status && <p className="settings-inline-feedback">{status}</p>}
    </details>
  );
}
