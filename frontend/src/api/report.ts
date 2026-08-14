import type { Finding } from '../types';
import { formatBeijingDateTime } from '../lib/time';
import { renderBehaviorFieldSvg } from '../brand/renderBehaviorFieldSvg';
import { escapeEvidenceHtml } from '../lib/finding-evidence-package';

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
    defect_family?: string;
  }>;
  dbFindings: Array<{ id: string; desc: string }>;
  modulesCovered?: number;
  releaseDecision?: 'go' | 'no-go' | 'conditional';
  releaseChecks?: Array<{ name: string; status: 'pass' | 'fail' | 'pending'; detail: string }>;
}

/** Build report data from display-ready findings (backend already returns Chinese labels). */
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
  modulesCovered?: number;
}) {
  return {
    modulesCovered: summary.modulesCovered,
    projectName: summary.projectName,
    generatedAt: formatBeijingDateTime(new Date()),
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
      expected: f.expected || '',
      actual: f.actual || '',
      evidence: f.evidence_quality ? `${f.evidence_quality.label} · ${f.evidence_quality.score}/100` : '',
      defect_family: f.defect_family,
    })),
    dbFindings: summary.dbFindings || [],
  };
}

export function renderReportHTML(d: ReportData): string {
  const getBEILabel = (s: number) => s >= 80 ? '稳健' : s >= 60 ? '关注' : '优先治理';
  const getScoreTone = (s: number) => s >= 80 ? 'tone-success' : s >= 60 ? 'tone-warning' : 'tone-danger';
  const getSeverityClass = (s: string) => s === 'P0' ? 'p0' : s === 'P1' ? 'p1' : 'p2';
  const getMetricTone = (value?: string) => value || 'tone-ink';
  const h = escapeEvidenceHtml;

  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QualiBug AI 风险评级报告 — ${h(d.projectName)}</title>
<style>
  :root {
    --ink: #0b1424; --muted: #64748b; --line: #e2e8f0;
    --primary: #5865f2; --success: #0ea571; --warning: #d97706; --danger: #e02449;
    --radius: 10px;
    --font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:#f8fafc;color:var(--ink);font:13px/1.6 var(--font);-webkit-font-smoothing:antialiased;padding:0}
  .report-actions{max-width:900px;margin:18px auto 0;padding:0 32px;text-align:right}
  .report-actions button{border:0;border-radius:8px;background:#0b1424;color:#fff;padding:9px 14px;font:600 13px var(--font);cursor:pointer}
  .report{max-width:900px;margin:0 auto;padding:40px 32px}
  
  /* Cover */
  .cover{text-align:center;padding:48px 0 40px;border-bottom:2px solid var(--line);margin-bottom:36px}
  .cover .logo{display:inline-flex;align-items:center;gap:10px;margin-bottom:20px}
  .cover .logo strong{font-size:22px;font-weight:800;letter-spacing:-.02em}
  .cover .logo strong .ai{color:#2563eb}
  .cover .logo span{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;font-weight:700}
  .cover h1{font-size:26px;font-weight:900;letter-spacing:-.02em;margin-bottom:8px}
  .cover .meta{color:var(--muted);font-size:13px}
  
  /* Score Row */
  .scores{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:32px}
  .score-card{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:24px;text-align:center}
  .score-card .label{font-size:11px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px}
  .score-card .value{font-size:42px;font-weight:900;letter-spacing:-.03em}
  .score-card .value span{font-size:16px;font-weight:600;color:var(--muted)}
  .score-card .sub{font-size:11px;color:var(--muted);margin-top:4px}
  .tone-success{color:var(--success)}
  .tone-warning{color:var(--warning)}
  .tone-danger{color:var(--danger)}
  .tone-primary{color:var(--primary)}
  .tone-ink{color:var(--ink)}
  
  /* Section */
  .section{margin-bottom:32px}
  .section h2{font-size:18px;font-weight:800;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid var(--primary)}
  .section-intro{font-size:12px;color:var(--muted);margin:-8px 0 14px}
  .section-subtitle{font-size:13px;font-weight:700;color:var(--muted);margin:16px 0 8px}
  .section-subtitle.spacing-top{margin-top:20px}
  .summary-copy{font-size:11px;color:var(--muted);margin:-4px 0 10px}
  .metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
  .metric-card{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:14px;text-align:center}
  .metric-card.detail{padding:18px 16px;text-align:left}
  .metric-value{font-size:24px;font-weight:900}
  .metric-card.detail .metric-value{font-size:28px;margin-bottom:4px}
  .metric-label{font-size:10px;color:var(--muted);font-weight:600;margin-top:2px}
  .metric-card.detail .metric-label{font-size:12px;font-weight:700;color:var(--ink);margin:0 0 6px}
  .metric-copy{font-size:10px;color:var(--muted);line-height:1.5}
  .empty-state{text-align:center;color:var(--muted);padding:40px}
  
  /* Finding Item */
  .finding{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:18px 20px;margin-bottom:12px;page-break-inside:avoid}
  .finding.p0{border-left:4px solid var(--danger)}
  .finding.p1{border-left:4px solid var(--warning)}
  .finding.p2{border-left:4px solid var(--primary)}
  .finding .head{display:flex;align-items:center;gap:10px;margin-bottom:10px}
  .finding .sev{padding:3px 10px;border-radius:4px;font-size:10px;font-weight:800;letter-spacing:.04em;white-space:nowrap}
  .finding .sev.p0{color:var(--danger);background:#fff1f2}
  .finding .sev.p1{color:var(--warning);background:#fffbeb}
  .finding .sev.p2{color:var(--primary);background:#eef2ff}
  .finding .title{font-size:14px;font-weight:700;flex:1}
  .finding .body{font-size:12px;color:var(--muted)}
  .finding .body .row{display:flex;gap:24px;margin-top:8px}
  .finding .body .row>div{flex:1}
  .finding .body .row label{display:block;font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px}
  .finding .body .row label.act-label{color:var(--danger)}
  .finding .body .row .exp{color:var(--ink)}
  .finding .body .row .act{color:var(--danger)}
  .finding .evidence-note{margin-top:8px;font-size:11px;color:var(--muted)}
  
  /* DB Finding */
  .db-finding{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:14px 18px;margin-bottom:8px;font-size:12px;display:flex;gap:10px;align-items:flex-start}
  .db-finding .tag{padding:2px 8px;border-radius:4px;font-size:9px;font-weight:800;background:var(--primary-muted, #eef2ff);color:var(--primary);white-space:nowrap;margin-top:2px}
  
  /* Footer */
  .footer{margin-top:40px;padding-top:20px;border-top:1px solid var(--line);text-align:center;color:var(--muted);font-size:11px}
  .footer b{color:var(--ink)}
  
  @media print {
    body{background:#fff}
    .report-actions{display:none}
    .report{max-width:100%;padding:20px}
    .finding,.db-finding{box-shadow:none;border:1px solid #ddd}
  }
</style>
</head>
<body>
<div class="report-actions"><button onclick="window.print()">打印 / 保存为 PDF</button></div>
<div class="report">

  <!-- Cover -->
  <div class="cover">
    <div class="logo">
      ${renderBehaviorFieldSvg({
        detail: 'compact',
        tone: 'light',
        width: 40,
        height: 40,
        idPrefix: 'report-brand',
        ariaHidden: true,
      })}
      <div><strong>QualiBug <span class="ai">AI</span></strong><span>风险决策台</span></div>
    </div>
    <h1>${h(d.projectName)} · 风险评级报告</h1>
    <p class="meta">生成时间：${h(d.generatedAt)} · 行业：${h(d.industry)}</p>
  </div>

  <!-- Scores -->
  <div class="scores">
    <div class="score-card">
      <div class="label">风险评级</div>
      <div class="value ${getScoreTone(d.beiScore)}">${d.beiScore}</div>
      <div class="sub">${getBEILabel(d.beiScore)}</div>
    </div>
    <div class="score-card">
      <div class="label">缺陷密度</div>
      <div class="value tone-warning">${h(d.bdsScore)}<span> 个</span></div>
      <div class="sub">每千个行为路径中高危缺陷</div>
    </div>
    <div class="score-card">
      <div class="label">多源自洽度</div>
      <div class="value ${d.bcsScore >= 80 ? 'tone-success' : 'tone-warning'}">${d.bcsScore}<span>%</span></div>
      <div class="sub">全部企业资料交叉验证一致率</div>
    </div>
  </div>

  <!-- Summary -->
  <div class="section">
    <h2>扫描摘要</h2>
    
    <h3 class="section-subtitle">风险层级分布</h3>
    <div class="metric-grid">
      ${[
        {l:'风险发现',v:d.totalFindings,t:'tone-ink'},
        {l:'P0 阻塞',v:d.p0Count,t:'tone-danger'},
        {l:'P1 高风险',v:d.p1Count,t:'tone-warning'},
        {l:'P2 提示',v:d.p2Count,t:'tone-primary'},
      ].map(m=>`<div class="metric-card">
        <div class="metric-value ${getMetricTone(m.t)}">${m.v}</div>
        <div class="metric-label">${m.l}</div>
      </div>`).join('')}
    </div>

    <h3 class="section-subtitle spacing-top">验证方式</h3>
    <p class="summary-copy">QualiBug AI 通过多种方式交叉验证，确保结论可追溯、可复现</p>
    <div class="metric-grid">
      ${[
        {l:'接口实时探测',v:d.runtimeProbes,sub:'对系统接口发起真实请求，验证实际行为',t:'tone-ink'},
        {l:'数据库直接检查',v:d.dbConfirmed,sub:'直连数据库，发现数据不一致、约束违反',t:d.dbConfirmed>0?'tone-danger':'tone-success'},
        {l:'文档交叉验证',v:d.bcsScore+'%',sub:'对比 PRD、API 文档、数据库 Schema 三方一致性',t:d.bcsScore>=80?'tone-success':'tone-warning'},
        {l:'静态规范扫描',v:d.findings.filter(f=>f.defect_family === 'api_contract' || f.defect_family === 'observability').length,sub:'扫描 OpenAPI 规范完整性与错误契约缺失',t:'tone-ink'},
      ].map(m=>`<div class="metric-card detail">
        <div class="metric-value ${getMetricTone(m.t)}">${m.v}</div>
        <div class="metric-label">${m.l}</div>
        <div class="metric-copy">${m.sub}</div>
      </div>`).join('')}
    </div>
  </div>

  ${d.modulesCovered || d.runtimeProbes ? `
  <div class="section">
    <h2>本轮检测事实</h2>
    <p class="section-intro">以下数字来自本轮真实执行与后端记账，不含人工工时或金额估算</p>
    <div class="metric-grid">
      <div class="metric-card detail">
        <div class="metric-value tone-ink">${d.runtimeProbes}</div>
        <div class="metric-label">真实执行验证点</div>
        <div class="metric-copy">对被测系统发起的真实接口探测数量</div>
      </div>
      <div class="metric-card detail">
        <div class="metric-value tone-danger">${d.p0Count} 个 P0</div>
        <div class="metric-label">阻断性风险拦截</div>
        <div class="metric-copy">发布前暴露的阻断性缺陷，需优先修复</div>
      </div>
      <div class="metric-card detail">
        <div class="metric-value tone-success">${d.modulesCovered || '—'}</div>
        <div class="metric-label">覆盖模块数</div>
        <div class="metric-copy">真实触达的业务模块，非理论覆盖</div>
      </div>
      <div class="metric-card detail">
        <div class="metric-value tone-ink">${d.dbConfirmed}</div>
        <div class="metric-label">数据库直接确认</div>
        <div class="metric-copy">直连数据库确认的数据异常数量</div>
      </div>
    </div>
  </div>` : ''}

  <!-- Findings -->
  <div class="section">
    <h2>风险详情 (${d.findings.length < d.totalFindings ? `展示前 ${d.findings.length} 条，共 ${d.totalFindings} 条` : `共 ${d.totalFindings} 条`})</h2>
    ${d.findings.map(f=>`
    <div class="finding ${getSeverityClass(f.severity)}">
      <div class="head">
        <span class="sev ${getSeverityClass(f.severity)}">${h(f.severity)}</span>
        <span class="title">${h(f.title)}</span>
      </div>
      <div class="body">
        ${f.expected||f.actual?`
        <div class="row">
          ${f.expected?`<div><label>预期行为</label><span class="exp">${h(f.expected.slice(0,150))}</span></div>`:''}
          ${f.actual?`<div><label class="act-label">实际行为</label><span class="act">${h(f.actual.slice(0,150))}</span></div>`:''}
        </div>`:''}
        ${f.evidence?`<div class="evidence-note">证据说明：${h(f.evidence)}</div>`:''}
      </div>
    </div>`).join('')}
    ${d.findings.length === 0 ? '<p class="empty-state">当前未检测到行为风险</p>' : ''}
  </div>

  <!-- DB Findings -->
  ${d.dbFindings.length > 0 ? `
  <div class="section">
    <h2>数据一致性隐患 (${d.dbFindings.length} 项)</h2>
    <p class="section-intro">直接从数据库检测到的数据异常，如计数负值、重复记录、引用失效等</p>
    ${d.dbFindings.map(f=>`
    <div class="db-finding">
      <span class="tag">${h(f.id)}</span>
      <span>${h(f.desc)}</span>
    </div>`).join('')}
  </div>` : ''}

  ${d.findings.some(f => f.evidence) ? `
  <div class="section">
    <h2>证据附录</h2>
    <p class="section-intro">关键缺陷的证据质量评分，所有证据均可在系统中回放验证</p>
    ${d.findings.filter(f => f.evidence).slice(0, 10).map(f => `
    <div class="db-finding">
      <span class="tag">${h(f.severity)}</span>
      <span><b>${h(f.title)}</b><br/>证据：${h(f.evidence)}</span>
    </div>`).join('')}
  </div>` : ''}

  ${d.releaseDecision || d.releaseChecks?.length ? `
  <div class="section">
    <h2>发布建议</h2>
    <div class="score-card" style="text-align:center;margin-bottom:20px;padding:28px">
      <div class="value ${d.releaseDecision === 'go' ? 'tone-success' : d.releaseDecision === 'no-go' ? 'tone-danger' : 'tone-warning'}" style="font-size:32px">
        ${d.releaseDecision === 'go' ? '✅ 建议发布' : d.releaseDecision === 'no-go' ? '🚫 不建议发布' : '⚠️ 有条件发布'}
      </div>
      <div class="sub">${d.releaseDecision === 'go' ? '所有门禁检查已通过' : d.releaseDecision === 'no-go' ? `${d.p0Count} 个 P0 缺陷未修复` : '部分检查待处理，需确认后放行'}</div>
    </div>
    ${d.releaseChecks?.length ? d.releaseChecks.map(c => `
    <div class="db-finding">
      <span class="tag" style="background:${c.status === 'pass' ? '#ecfdf5' : c.status === 'fail' ? '#fff1f3' : '#fff8eb'};color:${c.status === 'pass' ? '#0c9a6a' : c.status === 'fail' ? '#d91f45' : '#c9780a'}">${c.status === 'pass' ? '✓ 通过' : c.status === 'fail' ? '✗ 失败' : '⏳ 待处理'}</span>
      <span><b>${h(c.name)}</b> — ${h(c.detail)}</span>
    </div>`).join('') : ''}
  </div>` : ''}

  <!-- Footer -->
  <div class="footer">
    <p><b>QualiBug AI</b> · 风险决策台</p>
    <p class="section-intro">支持私有部署 / SaaS · 审计链路完整 · 所有判定可追溯到原始检测证据</p>
  </div>

</div>
</body>
</html>`;
}
