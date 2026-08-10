import type { Finding } from '../types';

const SENSITIVE_PATTERNS: Array<[RegExp, string]> = [
  [/\b(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+/gi, '$1[REDACTED]'],
  [/\b(cookie\s*[:=]\s*)[^\n]+/gi, '$1[REDACTED]'],
  [/\b(set-cookie\s*[:=]\s*)[^\n]+/gi, '$1[REDACTED]'],
  [/\b((?:access|refresh|id)?_?token\s*[:=]\s*)[^\s,;}&]+/gi, '$1[REDACTED]'],
  [/\b(api[_-]?key\s*[:=]\s*)[^\s,;}&]+/gi, '$1[REDACTED]'],
  [/\b(password\s*[:=]\s*)[^\s,;}&]+/gi, '$1[REDACTED]'],
  [/([?&](?:token|access_token|refresh_token|api_key|apikey|password)=)[^&#\s]+/gi, '$1[REDACTED]'],
];

export function redactExternalEvidence(value: unknown): string {
  let text = typeof value === 'string' ? value : value == null ? '' : String(value);
  for (const [pattern, replacement] of SENSITIVE_PATTERNS) {
    text = text.replace(pattern, replacement);
  }
  return text;
}

export function escapeEvidenceHtml(value: unknown): string {
  return redactExternalEvidence(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function moduleName(finding: Finding): string {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '未归类').trim() || '未归类';
}

function curatedEvidenceLines(finding: Finding): string[] {
  const lines = [
    `QualiBug 单问题证据包`,
    `问题 ID：${finding.id}`,
    `严重级别：${finding.severity}`,
    `标题：${finding.title}`,
    `影响模块：${moduleName(finding)}`,
    `业务影响：${finding.business_impact?.summary || finding.business_summary || '未上报'}`,
    `预期：${finding.expected || finding.expected_actual_comparison?.expected || '未指定'}`,
    `实际：${finding.actual || finding.expected_actual_comparison?.actual || '未捕获'}`,
    `证据质量：${finding.evidence_quality?.label || '未评分'}${finding.evidence_quality?.score != null ? `（${finding.evidence_quality.score}/100）` : ''}`,
    `复现率：${finding.proof?.repro_rate != null ? `${finding.proof.repro_rate}%` : '未上报'}`,
  ];

  if (finding.reproduction?.steps?.length) {
    lines.push('', '复现步骤：');
    finding.reproduction.steps.forEach((step, index) => lines.push(`${index + 1}. ${step}`));
  }

  if (finding.evidence_chain?.length) {
    lines.push('', '证据链：');
    finding.evidence_chain.forEach((step, index) => {
      lines.push(`${index + 1}. [${step.label || step.tag}] ${step.content || step.detail || ''}`);
    });
  }

  if (finding.investigation_guidance?.relevant_apis?.length) {
    lines.push('', `相关接口：${finding.investigation_guidance.relevant_apis.join('、')}`);
  }
  if (finding.investigation_guidance?.relevant_tables?.length) {
    lines.push(`相关表：${finding.investigation_guidance.relevant_tables.join('、')}`);
  }
  if (finding.investigation_guidance?.trace_id) {
    lines.push(`Trace ID：${finding.investigation_guidance.trace_id}`);
  }
  if (finding.regression_verification_obligations?.length) {
    lines.push('', `修复后验收：${finding.regression_verification_obligations.join('；')}`);
  }
  if (finding.regression) {
    lines.push(
      '',
      `回归生命周期：${finding.regression.lifecycle_label || finding.regression.lifecycle_status || '未报告'}`,
      `最近回归：${finding.regression.latest_status_label || finding.regression.latest_status || '未报告'}`,
      `回归门禁：${finding.regression.gate_status || '未报告'}`,
    );
  }

  lines.push('', '安全说明：本证据包由前端外发视图生成，敏感认证材料会进行模式脱敏；原始完整证据仍以 QualiBug 证据中心为准。');
  return lines;
}

export function buildFindingEvidencePackageText(finding: Finding): string {
  return curatedEvidenceLines(finding).map(redactExternalEvidence).join('\n');
}

export function buildFindingEvidencePackageHtml(finding: Finding): string {
  const lines = curatedEvidenceLines(finding).map((line) => escapeEvidenceHtml(line));
  const body = lines.map((line) => line ? `<div>${line}</div>` : '<br/>').join('');
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>QualiBug 证据包 - ${escapeEvidenceHtml(finding.id)}</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:#111827;margin:0;background:#f8fafc;line-height:1.65}
main{max-width:860px;margin:0 auto;background:#fff;min-height:100vh;padding:40px}
h1{font-size:24px;margin:0 0 8px} .meta{color:#64748b;margin-bottom:24px}.body{font-size:13px;white-space:pre-wrap;word-break:break-word}.actions{position:sticky;top:0;background:#fff;padding:12px 0 18px;border-bottom:1px solid #e2e8f0;margin-bottom:24px}.actions button{border:0;border-radius:8px;background:#111827;color:#fff;padding:9px 14px;cursor:pointer}.notice{margin-top:28px;padding:12px;background:#f1f5f9;color:#475569;border-radius:8px;font-size:12px}
@media print{body{background:#fff}.actions{display:none}main{max-width:none;padding:20px}.notice{break-inside:avoid}}
</style>
</head>
<body><main>
<div class="actions"><button onclick="window.print()">打印 / 保存为 PDF</button></div>
<h1>${escapeEvidenceHtml(finding.title)}</h1>
<div class="meta">${escapeEvidenceHtml(finding.severity)} · ${escapeEvidenceHtml(moduleName(finding))} · ${escapeEvidenceHtml(finding.id)}</div>
<div class="body">${body}</div>
<div class="notice">外发前仍应按企业数据安全制度复核；该页面不会生成公开链接，也不会自动上传到第三方服务。</div>
</main></body></html>`;
}
