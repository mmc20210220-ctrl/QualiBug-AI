import type { Finding } from '../types';

/** Map common English boilerplate to readable Chinese */
function toChinese(text: string): string {
  if (!text) return '';
  if (/[\u4e00-\u9fff]/.test(text)) return text;
  const patterns: [RegExp, string][] = [
    [/document and implement 401\/403 behavior/i, '应实现 401/403 认证失败响应'],
    [/no idempotency.*header.*documented/i, '缺少幂等性保障机制'],
    [/a permission-sensitive mutating operation does not document/i, '权限敏感操作未声明认证失败的错误响应'],
    [/declared responses:?\s*\[?'200'\]?/i, '仅声明了 200 响应，缺少 401/403 等错误码'],
    [/may start async work without observable progress/i, '异步操作缺少可观测的进度反馈'],
    [/lacks validation\/conflict error contract/i, '缺少参数校验和冲突处理的错误响应'],
    [/operation is missing operationid/i, '接口定义缺少 operationId'],
    [/no .*header parameter is documented/i, '缺少必要的请求头参数声明'],
    [/every operation should have a unique operationid/i, '每个接口应有唯一的 operationId 标识符'],
    [/openapi operation .*violates this/i, '该接口定义违反了 OpenAPI 规范要求'],
  ];
  for (const [re, r] of patterns) if (re.test(text)) return r;
  return text.length > 60 ? text.slice(0, 60) + '…' : text;
}

interface ReportData {
  projectName: string;
  generatedAt: string;
  beiScore: number;
  bdsScore: string;
  bcsScore: number;
  totalFindings: number;
  p0Count: number;
  p1Count: number;
  p2Count: number;
  industry: string;
  runtimeProbes: number;
  dbConfirmed: number;
  findings: Array<{
    severity: string;
    title: string;
    expected: string;
    actual: string;
    evidence: string;
  }>;
  dbFindings: Array<{ id: string; desc: string }>;
}

/** Build report data from the pipeline summary already loaded by Dashboard */
export function buildReportData(summary: {
  projectName: string;
  industry: string;
  totalBugs: number;
  beiScore: number;
  bdsScore: string;
  bcsScore: number;
  runtimeProbes: number;
  dbConfirmed: number;
  findings: Finding[];
  dbFindings?: Array<{ id: string; desc: string }>;
}): ReportData {
  return {
    projectName: summary.projectName,
    generatedAt: new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }),
    beiScore: summary.beiScore,
    bdsScore: summary.bdsScore,
    bcsScore: summary.bcsScore,
    totalFindings: summary.totalBugs,
    p0Count: summary.findings.filter(f => f.severity === 'P0').length,
    p1Count: summary.findings.filter(f => f.severity === 'P1').length,
    p2Count: summary.findings.filter(f => f.severity === 'P2').length,
    industry: summary.industry || '—',
    runtimeProbes: summary.runtimeProbes,
    dbConfirmed: summary.dbConfirmed,
    findings: summary.findings.slice(0, 30).map(f => ({
      severity: f.severity,
      title: f.title,
      expected: toChinese(f.expected),
      actual: toChinese(f.actual),
      evidence: f.proof?.hash || f.proof?.script_path || '',
    })),
    dbFindings: summary.dbFindings || [],
  };
}

export function renderReportHTML(d: ReportData): string {
  const getBEIColor = (s: number) => s >= 80 ? '#0ea571' : s >= 60 ? '#d97706' : '#e02449';
  const getBEILabel = (s: number) => s >= 80 ? '良好' : s >= 60 ? '一般' : '需关注';
  const sevStyle = (s: string) => s === 'P0' ? 'color:#e02449;background:#fff1f2' : s === 'P1' ? 'color:#d97706;background:#fffbeb' : 'color:#5865f2;background:#eef2ff';

  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QualiBug 行为风险评级报告 — ${d.projectName}</title>
<style>
  :root {
    --ink: #0b1424; --muted: #64748b; --line: #e2e8f0;
    --primary: #5865f2; --success: #0ea571; --warning: #d97706; --danger: #e02449;
    --radius: 10px;
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#f8fafc;color:var(--ink);font:13px/1.6 var(--font);-webkit-font-smoothing:antialiased;padding:0}
  .report{max-width:900px;margin:0 auto;padding:40px 32px}
  
  /* Cover */
  .cover{text-align:center;padding:48px 0 40px;border-bottom:2px solid var(--line);margin-bottom:36px}
  .cover .logo{display:inline-flex;align-items:center;gap:8px;margin-bottom:20px}
  .cover .logo strong{font-size:22px;font-weight:800;letter-spacing:-.02em}
  .cover .logo span{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;font-weight:700}
  .cover h1{font-size:26px;font-weight:900;letter-spacing:-.02em;margin-bottom:8px}
  .cover .meta{color:var(--muted);font-size:13px}
  
  /* Score Row */
  .scores{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:32px}
  .score-card{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:24px;text-align:center}
  .score-card .label{font-size:11px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px}
  .score-card .value{font-size:42px;font-weight:900;letter-spacing:-.03em}
  .score-card .sub{font-size:11px;color:var(--muted);margin-top:4px}
  
  /* Section */
  .section{margin-bottom:32px}
  .section h2{font-size:18px;font-weight:800;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid var(--primary)}
  
  /* Finding Item */
  .finding{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:18px 20px;margin-bottom:12px;page-break-inside:avoid}
  .finding.p0{border-left:4px solid var(--danger)}
  .finding.p1{border-left:4px solid var(--warning)}
  .finding.p2{border-left:4px solid var(--primary)}
  .finding .head{display:flex;align-items:center;gap:10px;margin-bottom:10px}
  .finding .sev{padding:3px 10px;border-radius:4px;font-size:10px;font-weight:800;letter-spacing:.04em;white-space:nowrap}
  .finding .title{font-size:14px;font-weight:700;flex:1}
  .finding .body{font-size:12px;color:var(--muted)}
  .finding .body .row{display:flex;gap:24px;margin-top:8px}
  .finding .body .row>div{flex:1}
  .finding .body .row label{display:block;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px}
  .finding .body .row .exp{color:var(--ink)}
  .finding .body .row .act{color:var(--danger)}
  
  /* DB Finding */
  .db-finding{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:14px 18px;margin-bottom:8px;font-size:12px;display:flex;gap:10px;align-items:flex-start}
  .db-finding .tag{padding:2px 8px;border-radius:4px;font-size:9px;font-weight:800;background:var(--primary-muted, #eef2ff);color:var(--primary);white-space:nowrap;margin-top:2px}
  
  /* Footer */
  .footer{margin-top:40px;padding-top:20px;border-top:1px solid var(--line);text-align:center;color:var(--muted);font-size:11px}
  .footer b{color:var(--ink)}
  
  @media print {
    body{background:#fff}
    .report{max-width:100%;padding:20px}
    .finding,.db-finding{box-shadow:none;border:1px solid #ddd}
  }
</style>
</head>
<body>
<div class="report">

  <!-- Cover -->
  <div class="cover">
    <div class="logo">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#5865f2" stroke-width="2"><path d="M12 3 20 6v5c0 5-3.3 8.5-8 10-4.7-1.5-8-5-8-10V6l8-3Z"/></svg>
      <div><strong>QualiBug</strong><span>行为风险终端</span></div>
    </div>
    <h1>${d.projectName} · 行为风险评级报告</h1>
    <p class="meta">生成时间：${d.generatedAt} · 行业：${d.industry}</p>
  </div>

  <!-- Scores -->
  <div class="scores">
    <div class="score-card">
      <div class="label">BEI · 行为暴露指数</div>
      <div class="value" style="color:${getBEIColor(d.beiScore)}">${d.beiScore}</div>
      <div class="sub">${getBEILabel(d.beiScore)}</div>
    </div>
    <div class="score-card">
      <div class="label">缺陷密度</div>
      <div class="value" style="color:var(--warning)">${d.bdsScore}<span style="font-size:16px;font-weight:600;color:var(--muted)"> 个</span></div>
      <div class="sub">每千个行为路径中高危缺陷</div>
    </div>
    <div class="score-card">
      <div class="label">多源自洽度</div>
      <div class="value" style="color:${d.bcsScore >= 80 ? 'var(--success)' : 'var(--warning)'}">${d.bcsScore}<span style="font-size:16px;font-weight:600;color:var(--muted)">%</span></div>
      <div class="sub">全部企业资料交叉验证一致率</div>
    </div>
  </div>

  <!-- Summary -->
  <div class="section">
    <h2>📊 扫描摘要</h2>
    
    <h3 style="font-size:13px;font-weight:700;color:var(--muted);margin:16px 0 8px">风险严重度分布</h3>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">
      ${[
        {l:'风险发现',v:d.totalFindings},
        {l:'P0 阻塞',v:d.p0Count,c:'var(--danger)'},
        {l:'P1 高风险',v:d.p1Count,c:'var(--warning)'},
        {l:'P2 提示',v:d.p2Count,c:'var(--primary)'},
      ].map(m=>`<div style="background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:14px;text-align:center">
        <div style="font-size:24px;font-weight:900;color:${m.c||'var(--ink)'}">${m.v}</div>
        <div style="font-size:10px;color:var(--muted);font-weight:600;margin-top:2px">${m.l}</div>
      </div>`).join('')}
    </div>

    <h3 style="font-size:13px;font-weight:700;color:var(--muted);margin:20px 0 8px">验证方式</h3>
    <p style="font-size:11px;color:var(--muted);margin:-4px 0 10px">QualiBug 通过多种方式交叉验证，确保发现可追溯、可复现</p>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px">
      ${[
        {l:'接口实时探测',v:d.runtimeProbes,sub:'对系统接口发起真实请求，验证实际行为'},
        {l:'数据库直接检查',v:d.dbConfirmed,sub:'直连数据库，发现数据不一致、约束违反',c:d.dbConfirmed>0?'var(--danger)':'var(--success)'},
        {l:'文档交叉验证',v:d.bcsScore+'%',sub:'对比 PRD、API 文档、数据库 Schema 三方一致性',c:d.bcsScore>=80?'var(--success)':'var(--warning)'},
        {l:'静态规范扫描',v:d.findings.filter(f=>f.severity==='P3'||f.title.includes('operationId')||f.title.includes('spec')).length,sub:'扫描 OpenAPI 规范完整性、错误契约缺失'},
      ].map(m=>`<div style="background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:18px 16px">
        <div style="font-size:28px;font-weight:900;color:${m.c||'var(--ink)'};margin-bottom:4px">${m.v}</div>
        <div style="font-size:12px;font-weight:700;color:var(--ink);margin-bottom:6px">${m.l}</div>
        <div style="font-size:10px;color:var(--muted);line-height:1.5">${m.sub}</div>
      </div>`).join('')}
    </div>
  </div>

  <!-- Findings -->
  <div class="section">
    <h2>🔍 行为风险详情 (${d.findings.length < d.totalFindings ? `展示前 ${d.findings.length} 条，共 ${d.totalFindings} 条` : `共 ${d.totalFindings} 条`})</h2>
    ${d.findings.map(f=>`
    <div class="finding ${f.severity.toLowerCase()}">
      <div class="head">
        <span class="sev" style="${sevStyle(f.severity)}">${f.severity}</span>
        <span class="title">${f.title}</span>
      </div>
      <div class="body">
        ${f.expected||f.actual?`
        <div class="row">
          ${f.expected?`<div><label style="color:var(--muted)">预期行为</label><span class="exp">${f.expected.slice(0,150)}</span></div>`:''}
          ${f.actual?`<div><label style="color:var(--danger)">实际行为</label><span class="act">${f.actual.slice(0,150)}</span></div>`:''}
        </div>`:''}
        ${f.evidence?`<div style="margin-top:8px;font-family:monospace;font-size:10px;color:var(--muted)">📎 ${f.evidence}</div>`:''}
      </div>
    </div>`).join('')}
    ${d.findings.length === 0 ? '<p style="text-align:center;color:var(--muted);padding:40px">✅ 未检测到行为风险</p>' : ''}
  </div>

  <!-- DB Findings -->
  ${d.dbFindings.length > 0 ? `
  <div class="section">
    <h2>🗄️ 数据一致性隐患 (${d.dbFindings.length} 项)</h2>
    <p style="font-size:12px;color:var(--muted);margin:-8px 0 14px">直接从数据库检测到的数据异常，如负库存、重复记录、引用失效等</p>
    ${d.dbFindings.map(f=>`
    <div class="db-finding">
      <span class="tag">${f.id}</span>
      <span>${f.desc}</span>
    </div>`).join('')}
  </div>` : ''}

  <!-- Footer -->
  <div class="footer">
    <p><b>QualiBug</b> · 行为风险评级基础设施</p>
    <p style="margin-top:4px">支持私有部署 / SaaS · 审计链路完整 · 所有判定可追溯到原始检测证据</p>
  </div>

</div>
</body>
</html>`;
}
