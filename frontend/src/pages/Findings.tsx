import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useFindingsData } from '../api/data';
import { getEvidenceSummaryText } from '../lib/evidence';
import { DEFECT_FAMILY_ORDER, getDefectFamilyLabel, type DefectFamilyId } from '../lib/finding-taxonomy';
import { usePageTitle } from '../lib/page-title';
import { formatBeijingDateTime } from '../lib/time';
import { useProjectNavigation } from '../lib/project-navigation';
import type { Finding } from '../types';

/** Convert structured/raw data into readable Chinese */
function formatText(text: string): string {
  if (!text) return '';
  // Already readable Chinese → return as-is
  if (/[\u4e00-\u9fff]/.test(text) && text.length > 10) return text;

  // Detect Python tuple arrays: [('ID', 0.0, -5.0), ...]
  const tupleMatch = text.match(/^\[(\([^)]+\)(?:,\s*\([^)]+\))*)\]$/);
  if (tupleMatch) {
    const items = text.match(/\(([^)]+)\)/g);
    if (items) {
      const sample = items.slice(0, 3).map(t => t.replace(/[()]/g, '').split(',')[0].trim().replace(/'/g, '')).join('、');
      if (items.length > 3) return `${items.length} 条记录异常，如：${sample} 等`;
      return `发现 ${items.length} 条异常记录：${sample}`;
    }
  }

  // Detect key-value evidence like "18 条: ['RM-001', ...]"
  const kvMatch = text.match(/^(\d+)\s*条?\s*:\s*\[(.+)\]$/);
  if (kvMatch) {
    const count = kvMatch[1];
    const items = kvMatch[2].split(',').slice(0, 5).map(s => s.trim().replace(/['"]/g, '')).join('、');
    return `${count} 条记录，涉及：${items}${kvMatch[2].split(',').length > 5 ? ' 等' : ''}`;
  }

  // Detect txn_type evidence like "[{'txn_type': 'ISSUE', ...}]"
  if (text.includes("'txn_type'") || text.includes('"txn_type"')) {
    const types = [...text.matchAll(/['"]txn_type['"]\s*:\s*['"](\w+)['"]/g)].map(m => m[1]);
    const unique = [...new Set(types)];
    return `发现 ${unique.join('、')} 类型流水异常`;
  }

  // Fall back to English→Chinese
  return toChinese(text);
}

function toChinese(text: string): string {
  if (!text) return '';
  if (/[\u4e00-\u9fff]/.test(text)) return text;

  const patterns: [RegExp, string][] = [
    [new RegExp('document and implement 401/403 behavior', 'i'), '应实现 401/403 认证失败响应'],
    [/no idempotency.*header.*documented/i, '缺少幂等性保障机制（无 Idempotency-Key 请求头）'],
    [/the operation looks like.*no idempotency/i, '存在重复提交风险，未声明幂等键'],
    [/a permission-sensitive mutating operation does not document/i, '权限敏感操作未声明认证失败的错误响应'],
    [/declared responses:?\s*\[?'200'\]?/i, '仅声明了 200 响应，缺少 401/403 等错误码'],
    [/may start async work without observable progress/i, '异步操作缺少可观测的进度反馈'],
    [new RegExp('lacks validation/conflict error contract', 'i'), '缺少参数校验和冲突处理的错误响应'],
    [/operation is missing operationid/i, '接口定义缺少 operationId 标识'],
    [/no .*header parameter is documented/i, '缺少必要的请求头参数声明'],
    [/require an idempotency key/i, '需要引入幂等键机制防止重复处理'],
    [/every operation should have a unique operationid/i, '每个接口应有唯一的 operationId 标识符'],
    [/openapi operation .*violates this/i, '该接口定义违反了 OpenAPI 规范要求'],
    [/open the openapi document/i, '打开 OpenAPI 规范文档'],
    [new RegExp('inspect (POST|GET|PUT|PATCH|DELETE) (/[^\\s]+)', 'i'), '检查对应接口定义，对照 PRD 文档验证'],
    [/compare the operation against the prd/i, '对照 PRD 文档验证接口行为'],
    [/send invalid, unauthorized, duplicate/i, '发送非法、未授权、重复的请求'],
    [/verify documented 4xx responses/i, '验证接口是否返回正确的 4xx 错误响应'],
    [/confirm no business side effects/i, '确认被拒绝的请求未产生业务副作用'],
    [/submit the same .*request twice/i, '重复发送相同请求两次'],
    [/verify only one business side effect is created/i, '验证仅产生一次业务效果，第二次应为幂等返回'],
    [/repeat with the same request identifier/i, '使用相同的幂等键再次发送请求'],
    [new RegExp('document and implement 401/403 and 404', 'i'), '应实现 401/403/404 等标准错误响应'],
    [/upload and ingest a document/i, '文档上传与解析'],
    [/save test environment configuration/i, '测试环境配置'],
    [/register enterprise tool connector/i, '企业工具连接器注册'],
    [/rebuild knowledge center/i, '知识库重建'],
    [/run autonomous bug scanning/i, '自主扫描引擎'],
    [/save llm configuration/i, '智能引擎配置保存'],
    [/delete a knowledge source/i, '知识源删除'],
    [/enqueue a new pilot task/i, '任务队列'],
    [/approve a pending task/i, '任务审批'],
    [/run next queued task/i, '任务执行'],
    [/get pilot overview json/i, '项目概览'],
    [/get knowledge asset json/i, '知识资产'],
    [/get file content for preview/i, '文件预览'],
    [/service health check/i, '健康检查'],
  ];

  for (const [re, replacement] of patterns) {
    if (re.test(text)) return replacement;
  }

  // Fallback: truncate and mark as raw
  return text.length > 60 ? text.slice(0, 60) + '…' : text;
}

/** Generate generic business-facing operation steps based on bug type and endpoint */
function getActionableSteps(f: Finding): string[] {
  const t = f.title.toLowerCase();
  const method = f.repro_method || 'GET';
  const path = f.repro_path || '';
  const family = f.defect_family;

  if (family === 'security_boundary' || family === 'privacy_compliance') {
    return [
      '切换为低权限或未授权身份进入系统',
      `尝试触发 ${method} ${path || '对应业务操作'}`,
      '确认系统是否正确拒绝访问，并检查是否存在越权或敏感信息暴露',
    ];
  }

  if (family === 'data_integrity') {
    return [
      `执行 ${method} ${path || '对应业务操作'}`,
      '记录操作前后的关键业务数据与状态变化',
      '核对数据库或业务账本，确认是否存在不一致、重复处理或守恒破坏',
    ];
  }

  if (family === 'api_contract' || family === 'observability') {
    return [
      '打开对应接口文档或错误响应约定',
      `核对 ${method} ${path || '接口'} 的参数、响应码和错误返回`,
      '对照实际请求结果，确认契约、错误码或追踪信息是否缺失',
    ];
  }

  if (family === 'ui' || family === 'uiux' || family === 'accessibility_i18n') {
    return [
      '进入对应前端页面并按真实操作路径执行任务',
      '观察页面渲染、交互反馈、文案与导航是否正常',
      '确认是否存在空白页、操作断链、提示误导或本地化展示异常',
    ];
  }

  // Auth / permission verification
  if (t.includes('401') || t.includes('403') || t.includes('lacks') || t.includes('error contract')) {
    return [
      '退出登录，以未认证状态访问系统',
      `尝试访问: ${method} ${path}`,
      '预期返回 401/403 未授权错误，若成功则确认权限校验缺失',
    ];
  }

  // Idempotency
  if (t.includes('idempotenc') || t.includes('幂等') || t.includes('replay')) {
    return [
      `执行一次 ${method} ${path} 操作`,
      '立即再次执行相同的操作（模拟重复提交）',
      '预期应提示已处理或返回相同结果，若产生重复数据则确认缺陷',
    ];
  }

  // Database consistency
  if (t.includes('db verified') || t.includes('数据') || t.includes('consistency')) {
    return [
      `在前端执行 ${method} ${path} 对应的业务操作`,
      '记录操作前后的数据显示',
      '登录数据库执行对应查询，验证数据一致性',
    ];
  }

  // Business rule / PRD
  if (t.includes('prd') || t.includes('规则') || t.includes('contract')) {
    return [
      '打开被检测接口对应的前端页面',
      '执行符合 PRD 描述的正常业务操作',
      '对比操作结果与 PRD 中的业务规则描述是否一致',
    ];
  }

  // Spec / API definition
  if (t.includes('operationid') || t.includes('spec')) {
    return [
      '打开 API 文档页面',
      '检查对应接口是否有完整定义（参数、响应码、错误处理）',
      '对比 PRD 检查接口定义是否完整',
    ];
  }

  // Path-based generic steps (no industry-specific assumptions)
  if (method === 'POST' && path)
    return ['登录对应角色账号', `在对应页面执行创建/提交操作`, '观察提交后数据是否正确生成'];
  if (method === 'PUT' || method === 'PATCH')
    return ['登录对应角色账号', `在对应页面执行编辑/更新操作`, '观察修改后数据是否正确更新'];
  if (method === 'DELETE')
    return ['登录对应角色账号', `在对应页面执行删除操作`, '确认删除后数据是否被正确移除'];
  if (path)
    return ['登录对应角色账号进入系统', `访问 ${method} ${path} 对应的前端页面`, '按照正常业务流程执行操作'];
  return ['确认缺陷涉及的系统和模块', '按照 PRD 中描述的业务流程操作', '观察系统实际行为与 PRD 预期的偏差'];
}

type FindingType = 'all' | 'P0' | 'P1' | 'P2' | DefectFamilyId | 'quality_gap';

/** Human-readable display name for a filter value. */
function getFilterDisplayName(f: FindingType): string {
  if (f === 'all') return '全部';
  if (f === 'P0') return 'P0 严重缺陷';
  if (f === 'P1') return 'P1 一般缺陷';
  if (f === 'P2') return 'P2 轻微缺陷';
  if (f === 'quality_gap') return '质量保障缺口';
  return getDefectFamilyLabel(f);
}

export function Findings() {
  usePageTitle('行为验证');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const { findings, loading, error, refetch } = useFindingsData(project);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<FindingType>('all');
  const p0Count = findings.filter(f => f.severity === 'P0').length;
  const p1Count = findings.filter(f => f.severity === 'P1').length;
  const p2Count = findings.filter(f => f.severity === 'P2').length;
  const qualityGapCount = findings.filter(f => f.quality_assurance_gap).length;
  const familyStats = DEFECT_FAMILY_ORDER
    .map((family) => ({
      family,
      label: getDefectFamilyLabel(family),
      count: findings.filter((finding) => finding.defect_family === family).length,
    }))
    .filter((item) => item.count > 0);
  const activeFamilyCount = familyStats.length;
  const apiContractCount = findings.filter((f) => f.defect_family === 'api_contract').length;
  const dataIntegrityCount = findings.filter((f) => f.defect_family === 'data_integrity').length;
  const securityCount = findings.filter((f) => f.defect_family === 'security_boundary' || f.defect_family === 'privacy_compliance').length;
  const frontendCount = findings.filter((f) => ['ui', 'uiux', 'accessibility_i18n'].includes(f.defect_family)).length;

  const filters: Array<{ label: string; value: FindingType }> = [
    { label: `全部 (${findings.length})`, value: 'all' },
    ...familyStats.map((item) => ({ label: `${item.label} (${item.count})`, value: item.family })),
    ...(qualityGapCount > 0 ? [{ label: `保障缺口 (${qualityGapCount})`, value: 'quality_gap' as const }] : []),
    { label: `P0`, value: 'P0' },
    { label: `P1`, value: 'P1' },
    { label: `P2`, value: 'P2' },
  ];

  const displayData = (() => {
    if (filter === 'all') return findings;
    if (filter === 'P0' || filter === 'P1' || filter === 'P2') return findings.filter(f => f.severity === filter);
    if (filter === 'quality_gap') return findings.filter(f => f.quality_assurance_gap);
    return findings.filter(f => f.defect_family === filter);
  })();

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">风险清单</span>
          <h1>行为验证</h1>
          <p>把系统行为偏差收敛为可追溯、可复现、可分派的整改清单。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">共 {findings.length} 条风险记录</span>
            <span className="summary-pill">高优先级 {p0Count + p1Count}</span>
            <span className="summary-pill">已覆盖类型 {activeFamilyCount}</span>
            <span className="summary-pill">保障缺口 {qualityGapCount}</span>
          </div>
        </div>
        {error && (
          <button className="btn btn-secondary" onClick={refetch}>重新加载</button>
        )}
      </div>

      <div className="findings-stat-grid mb-4">
        {[
          { label: 'P0 阻断', value: p0Count, note: '需要立即闭环', tone: 'danger' },
          { label: 'P1 高风险', value: p1Count, note: '影响发布与履约', tone: 'warning' },
          { label: 'P2 提示', value: p2Count, note: '建议纳入回归', tone: 'primary' },
          { label: '已覆盖类型', value: activeFamilyCount, note: '已命中全谱分类维度', tone: 'neutral' },
        ].map((item) => (
          <article key={item.label} className={`findings-stat-card tone-${item.tone}`}>
            <strong>{item.value}</strong>
            <span>{item.label}</span>
            <small>{item.note}</small>
          </article>
        ))}
      </div>

      {/* Filters */}
      <div className="filters behavior-filters mb-4">
        {filters.map(f => (
          <button key={f.value} onClick={() => setFilter(f.value)}
            className={`filter${filter === f.value ? ' active' : ''}`}>{f.label}</button>
        ))}
      </div>
      {!loading && findings.length > 0 && (
        <div className="page-summary-strip mb-4">
          <span className="summary-pill">接口契约 {apiContractCount}</span>
          <span className="summary-pill">数据一致性 {dataIntegrityCount}</span>
          <span className="summary-pill">安全边界/隐私 {securityCount}</span>
          <span className="summary-pill">前端体验 {frontendCount}</span>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="state-panel">
          <div className="spinner spinner-centered" />
          <p>正在整理风险清单...</p>
        </div>
      )}

      {/* Error with no data */}
      {!loading && error && displayData.length === 0 && (
        <section className="findings-empty-state danger">
          <span className="findings-empty-kicker">连接异常</span>
          <h3>风险数据暂时不可用</h3>
          <p>{error}</p>
          <button className="btn btn-primary" onClick={refetch}>重新连接</button>
        </section>
      )}

      {/* Empty state — all data */}
      {!loading && !error && displayData.length === 0 && filter === 'all' && (
        <section className="findings-empty-state">
          <span className="findings-empty-kicker">当前空态</span>
          <h3>暂无风险发现</h3>
          <p>系统尚未检测到行为偏差。运行一次扫描后，这里会自动切换为真实风险清单。</p>
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/dashboard', project)}>
            前往总览启动扫描
          </button>
        </section>
      )}

      {/* Empty state — filter has no matches */}
      {!loading && !error && displayData.length === 0 && filter !== 'all' && (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">筛选结果</span>
          <h3>当前没有对应风险</h3>
          <p>无 {getFilterDisplayName(filter)} 类型风险发现。</p>
          <button className="btn btn-secondary" onClick={() => setFilter('all')}>查看全部</button>
        </section>
      )}

      {/* Findings List */}
      {displayData.map(f => {
        const isOpen = expandedId === f.id;
        const steps = f.reproduce_steps_business?.length > 0 ? f.reproduce_steps_business : getActionableSteps(f);

        return (
          <div key={f.id} className={`evidence-item findings-item ${f.severity.toLowerCase()}${isOpen ? ' open' : ''}`}>
            <div className="evidence-head" onClick={() => setExpandedId(isOpen ? null : f.id)}>
              <span className={`severity ${f.severity.toLowerCase()}`}>{f.severity}</span>
              <span className="evidence-title">{f.title}</span>
              <span className="evidence-meta">
                <span>复现 {f.reproducibility_count} 次</span>
                <time>{formatBeijingDateTime(f.timestamp)}</time>
              </span>
              <span className="evidence-expand">{isOpen ? '▲' : '▼'}</span>
            </div>
            <div className="evidence-body">
              {/* Expected vs Actual */}
              <div className="findings-compare-grid">
                <div className="findings-compare-card">
                  <span className="findings-compare-label">预期行为</span>
                  <p>{formatText(f.expected) || '未指定'}</p>
                </div>
                <div className="findings-compare-card danger">
                  <span className="findings-compare-label">实际行为</span>
                  <p>{formatText(f.actual) || '未捕获'}</p>
                </div>
              </div>

              {/* Mini evidence chain */}
              {f.evidence_chain.length > 0 && (
                <div className="evidence-chain">
                  {f.evidence_chain.map((step, i) => (
                    <div className="chain-step" key={i}>
                      <span className={`step-tag ${step.tag}`}>{step.label}</span>
                      <strong>{step.content}</strong>
                      <code>{step.detail}</code>
                    </div>
                  ))}
                </div>
              )}

              {/* Reproduction - two-column: API + UI */}
              {(f.repro_method && f.repro_path || f.repro_steps.length > 0) && (
                <div className={`findings-repro-grid${f.repro_method && f.repro_path ? ' dual' : ''}`}>
                  {/* API column */}
                  {(f.repro_method && f.repro_path) && (
                    <div className="findings-repro-panel code">
                      <div className="findings-panel-kicker">接口复现</div>
                      <div className="findings-command">
                        <code className="tone-success">curl</code>
                        <code className="tone-default"> -X {f.repro_method} </code>
                        <code className="tone-warning">&quot;{f.repro_path}&quot;</code>
                      </div>
                      {f.repro_method !== 'GET' && (
                        <div className="findings-command">
                          <code className="tone-muted"> -H &quot;Content-Type: application/json&quot; -d '{'{...}'}'</code>
                        </div>
                      )}
                      <div className="findings-panel-note">复制到终端或导入 Postman 即可执行</div>
                    </div>
                  )}
                  {/* UI/operation column */}
                  <div className="findings-repro-panel business">
                    <div className="findings-panel-kicker">前端操作复现</div>
                    <ol className="findings-steps">
                      {steps.map((step, i) => (
                        <li key={i}>{step}</li>
                      ))}
                    </ol>
                    {f.repro_method && (
                      <div className="findings-panel-note strong">
                        对应接口：{f.repro_method} {f.repro_path}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Repro steps */}
              {/* Investigation guide */}
              {(f.repro_path || f.source_entity || f.evidence_hint) && (
                <div className="findings-investigation-card">
                  <div className="findings-panel-kicker warning">排查指引</div>
                  <div className="findings-investigation-body">
                    {f.repro_path && (
                      <div>请求路径：<code>{f.repro_method} {f.repro_path}</code></div>
                    )}
                    {f.source_entity && (
                      <div>涉及模块：{toChinese(f.source_entity)}</div>
                    )}
                    {f.evidence_hint && (
                      <div>排查线索：{f.evidence_hint}</div>
                    )}
                    {f.investigation_guidance?.log_search && (
                      <div><code>{f.investigation_guidance.log_search}</code></div>
                    )}
                    {f.investigation_guidance?.sql_verify && (
                      <div><code>{f.investigation_guidance.sql_verify}</code></div>
                    )}
                    <div className="findings-investigation-note">
                      {f.repro_path
                        ? '在日志系统中搜索请求返回的 Trace ID，或按以上路径过滤请求日志即可定位。'
                        : '在数据库中检查对应表数据，通过数据变更日志追溯相关业务操作即可定位。'}
                    </div>
                  </div>
                </div>
              )}

              {/* Proof bar */}
              <div className="evidence-proof">
                <svg viewBox="0 0 24 24" width="16" height="16"><path d="M20 6 9 17l-5-5" /></svg>
                <div>
                  <strong>证据验证{f.proof.repro_rate >= 80 ? '通过' : '中'} · 复现率 {f.proof.repro_rate}%</strong>
                  <p>{getEvidenceSummaryText(f)}</p>
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default Findings;
