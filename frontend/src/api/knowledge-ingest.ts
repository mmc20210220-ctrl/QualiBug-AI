import { fetchWithAuth } from './client';

const MAX_FILE_BYTES = 10 * 1024 * 1024;
const SUPPORTED_EXTENSIONS = new Set([
  '.md', '.markdown', '.txt', '.rst', '.html', '.htm',
  '.yaml', '.yml', '.json', '.csv', '.sql', '.xml',
  '.svg', '.har', '.log', '.pdf', '.docx', '.xlsx', '.xls',
]);

export type KnowledgeIngestResult = {
  ok: boolean;
  source_id?: string;
  filename?: string;
  doc_type?: string;
  source_type_resolution?: 'automatic' | 'automatic_fallback' | 'explicit_override' | string;
  ingest_status?: string;
  auto_scan?: 'triggered' | 'deferred' | 'skipped' | 'not_applicable' | string;
  auto_scan_reason?: string;
  message?: string;
  error?: string;
};

function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot >= 0 ? filename.slice(dot).toLowerCase() : '';
}

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

async function responsePayload(response: Response): Promise<Record<string, unknown>> {
  try {
    const payload = await response.json();
    return payload && typeof payload === 'object' && !Array.isArray(payload)
      ? payload as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

async function ingestKnowledgeFile(
  project: string,
  file: File,
  options: { deferAutoScan: boolean; finalizeBatch: boolean },
): Promise<KnowledgeIngestResult> {
  const projectId = project.trim();
  if (!projectId) throw new Error('请先选择客户项目。');
  const extension = extensionOf(file.name);
  if (!SUPPORTED_EXTENSIONS.has(extension)) {
    throw new Error(`暂不支持 ${extension || '无扩展名'} 文件：${file.name}`);
  }
  if (file.size <= 0) throw new Error(`文件为空：${file.name}`);
  if (file.size > MAX_FILE_BYTES) throw new Error(`单个文件不能超过 10 MB：${file.name}`);

  const content = toBase64(await file.arrayBuffer());
  const response = await fetchWithAuth('/api/knowledge/ingest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: projectId,
      filename: file.name,
      content,
      defer_auto_scan: options.deferAutoScan,
      finalize_batch: options.finalizeBatch,
    }),
  });
  const payload = await responsePayload(response);
  if (!response.ok || payload.ok !== true) {
    const message = typeof payload.message === 'string'
      ? payload.message
      : typeof payload.error === 'string'
        ? payload.error
        : `资料导入失败：HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload as KnowledgeIngestResult;
}

export async function ingestKnowledgeFiles(project: string, files: File[]): Promise<KnowledgeIngestResult[]> {
  const selected = files.filter((file) => file.size > 0);
  if (selected.length === 0) throw new Error('没有可导入的文件。');

  const results: KnowledgeIngestResult[] = [];
  for (let index = 0; index < selected.length; index += 1) {
    results.push(await ingestKnowledgeFile(project, selected[index], {
      deferAutoScan: index < selected.length - 1,
      finalizeBatch: index === selected.length - 1,
    }));
  }
  return results;
}

export const KNOWLEDGE_UPLOAD_ACCEPT = [...SUPPORTED_EXTENSIONS].join(',');
