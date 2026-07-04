import type { Finding } from '../types';

type EvidenceLike = Pick<
  Finding,
  'title' | 'severity' | 'repro_method' | 'repro_path' | 'source_entity' | 'docRefs' | 'evidence_chain' | 'investigation_guidance'
>;

function clean(value: string | undefined) {
  return String(value || '').trim();
}

export function getEvidenceSummaryText(finding: EvidenceLike) {
  const parts: string[] = [];

  if (clean(finding.repro_path)) parts.push('接口复现链路已附');
  if (clean(finding.source_entity)) parts.push(`数据核验线索：${clean(finding.source_entity)}`);
  if ((finding.docRefs || []).length > 0) parts.push('资料交叉验证已附');
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
    return `-- 核查 ${entity} 相关业务数据\n-- 对比关键字段、状态流转与预期结果是否一致`;
  }
  return '-- 核查关联业务数据\n-- 对比请求前后状态变化与关键字段是否符合预期';
}

export function getEvidenceLogHint(finding: EvidenceLike) {
  const path = clean(finding.repro_path);
  if (path) {
    const method = clean(finding.repro_method) || 'GET';
    return `# 按接口路径检索相关日志\n# 关键词：${method.toUpperCase()} ${path}\n# 结合请求时间窗口、返回码与业务主键交叉定位`;
  }
  return '# 按缺陷标题、发生时间与业务对象检索日志\n# 结合异常返回与状态变更记录交叉定位';
}
