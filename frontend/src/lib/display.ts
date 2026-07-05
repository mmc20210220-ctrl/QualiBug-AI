export function formatDurationMs(value: number | string | null | undefined, fallback = '暂无') {
  const duration = Number(value);
  if (!Number.isFinite(duration) || duration <= 0) return fallback;

  if (duration < 10) {
    return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(duration)}ms`;
  }

  if (duration < 1000) {
    return `${Math.round(duration)}ms`;
  }

  const seconds = duration / 1000;
  if (seconds < 60) {
    return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(seconds)}s`;
  }

  const totalSeconds = Math.round(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const remainSeconds = totalSeconds % 60;
  return remainSeconds ? `${minutes}m ${remainSeconds}s` : `${minutes}m`;
}

export function formatActorName(value: string | null | undefined) {
  const actor = String(value || '').trim();
  if (!actor) return '';

  const normalized = actor.toLowerCase();
  if (normalized === 'admin' || normalized === 'administrator') {
    return '管理账号';
  }

  if (normalized === 'root' || normalized === 'super_admin' || normalized === 'superadmin' || normalized === 'owner') {
    return '高权限账号';
  }

  if (normalized === 'system' || normalized === 'service' || normalized === 'robot' || normalized === 'bot') {
    return '系统账号';
  }

  return actor;
}

export function formatStatusCodeLabel(value: number | string | null | undefined) {
  const code = Number(value);
  if (!Number.isFinite(code) || code <= 0) return '';
  if (code >= 500) return '服务端错误';
  if (code >= 400) return '客户端错误';
  if (code >= 300) return '重定向';
  if (code >= 200) return '成功';
  if (code >= 100) return '请求处理中';
  return '';
}

function normalizeInlineResponseText(content: string) {
  if (!content) return '';

  return content
    .replace(/耗时\s*([0-9]+(?:\.[0-9]+)?)\s*ms/gi, (_, value: string) => `耗时 ${formatDurationMs(value)}`)
    .replace(/操作者[:：]?\s*([A-Za-z0-9_-]+)/gi, (_, value: string) => `操作者 ${formatActorName(value)}`);
}

export function formatResponseSummary(
  content: string,
  structured?: { status_code?: unknown; duration_ms?: unknown; actor?: unknown },
) {
  const parts: string[] = [];
  const code = Number(structured?.status_code);
  const duration = Number(structured?.duration_ms);
  const actor = formatActorName(structured?.actor ? String(structured.actor) : '');

  if (Number.isFinite(code) && code > 0) {
    const label = formatStatusCodeLabel(code);
    parts.push(label ? `状态码 ${code}（${label}）` : `状态码 ${code}`);
  }

  if (Number.isFinite(duration) && duration > 0) {
    parts.push(`耗时 ${formatDurationMs(duration)}`);
  }

  if (actor) {
    parts.push(`操作者 ${actor}`);
  }

  if (parts.length > 0) return parts.join(' · ');
  return normalizeInlineResponseText(content);
}
