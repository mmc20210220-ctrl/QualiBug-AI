/** @deprecated 后端 display_ready_formatter.py 已返回 display 版本的 evidence 字段，此文件仅保留函数签名作 fallback。 */
import type { Finding } from '../types';

type EvidenceLike = Pick<
  Finding,
  'title' | 'severity' | 'repro_method' | 'repro_path' | 'source_entity' | 'doc_refs' | 'evidence_chain' | 'investigation_guidance' | 'evidence_quality'
>;

function clean(value: string | undefined) {
  return String(value || '').trim();
}

export function getEvidenceSummaryText(finding: EvidenceLike) {
  if (finding.evidence_quality) {
    return `${finding.evidence_quality.label} · ${finding.evidence_quality.score}/100 · ${finding.evidence_quality.summary}`;
  }

  const parts: string[] = [];

  if (clean(finding.repro_path)) parts.push('接口复现链路已附');
  if (clean(finding.source_entity)) parts.push(`数据核验线索：${clean(finding.source_entity)}`);
  if ((finding.doc_refs || []).length > 0) parts.push('资料交叉验证已附');
  if (parts.length === 0 && (finding.evidence_chain || []).length > 0) parts.push('检测证据已归档');

  return parts.join(' / ') || '检测证据已归档';
}

export function getEvidenceLocatorText(finding: EvidenceLike) {
  const area = clean(finding.investigation_guidance?.primary_area);
  if (area) return `优先核查：${area}`;

  const entity = clean(finding.source_entity);
  if (entity) return `优先核查：${entity} 相关状态与写入链路`;

  const path = clean(finding.repro_path);
  if (path) {
    const method = clean(finding.repro_method) || 'GET';
    return `优先核查：${method.toUpperCase()} ${path} 的鉴权、校验与幂等逻辑`;
  }

  return `优先核查：${clean(finding.title) || `${finding.severity} 风险项`}`;
}

export function getEvidenceSqlHint(finding: EvidenceLike) {
  const entity = clean(finding.source_entity);
  if (entity) {
    return `-- 企业核验目标：${entity}\n-- 1. 导出请求前业务主键、状态、金额、归属用户\n-- 2. 执行复现动作后再次导出相同字段\n-- 3. 对比状态流转、金额正负、权限归属与 PRD / API 规则是否一致`;
  }
  return '-- 当前缺少业务主键或表字段，无法形成可审计 SQL 证据\n-- 请补充业务主键 / 关联字段，并导出请求前后 DB 快照';
}

export function getEvidenceLogHint(finding: EvidenceLike) {
  const path = clean(finding.repro_path);
  if (path) {
    const method = clean(finding.repro_method) || 'GET';
    return `# 按接口路径检索相关日志\n# 关键词：${method.toUpperCase()} ${path}\n# 必须补齐：请求时间窗口、traceId / requestId、状态码、业务主键\n# 与响应体、DB 快照交叉验证后再标记为已验证缺陷`;
  }
  return '# 当前缺少可检索接口路径或页面地址\n# 请先补跑真实请求 / 浏览器用例，记录时间戳、traceId、状态码和错误摘要';
}
