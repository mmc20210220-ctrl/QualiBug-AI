import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useFindingsData } from '../api/data';
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
    [/document and implement 401\/403 behavior/i, '应实现 401/403 认证失败响应'],
    [/no idempotency.*header.*documented/i, '缺少幂等性保障机制（无 Idempotency-Key 请求头）'],
    [/the operation looks like.*no idempotency/i, '存在重复提交风险，未声明幂等键'],
    [/a permission-sensitive mutating operation does not document/i, '权限敏感操作未声明认证失败的错误响应'],
    [/declared responses:?\s*\[?'200'\]?/i, '仅声明了 200 响应，缺少 401/403 等错误码'],
    [/may start async work without observable progress/i, '异步操作缺少可观测的进度反馈'],
    [/lacks validation\/conflict error contract/i, '缺少参数校验和冲突处理的错误响应'],
    [/operation is missing operationid/i, '接口定义缺少 operationId 标识'],
    [/no .*header parameter is documented/i, '缺少必要的请求头参数声明'],
    [/require an idempotency key/i, '需要引入幂等键机制防止重复处理'],
    [/every operation should have a unique operationid/i, '每个接口应有唯一的 operationId 标识符'],
    [/openapi operation .*violates this/i, '该接口定义违反了 OpenAPI 规范要求'],
    [/open the openapi document/i, '打开 OpenAPI 规范文档'],
    [/inspect (POST|GET|PUT|PATCH|DELETE) (\/[\w\/{}]+)/i, '检查对应接口定义，对照 PRD 文档验证'],
    [/compare the operation against the prd/i, '对照 PRD 文档验证接口行为'],
    [/send invalid, unauthorized, duplicate/i, '发送非法、未授权、重复的请求'],
    [/verify documented 4xx responses/i, '验证接口是否返回正确的 4xx 错误响应'],
    [/confirm no business side effects/i, '确认被拒绝的请求未产生业务副作用'],
    [/submit the same .*request twice/i, '重复发送相同请求两次'],
    [/verify only one business side effect is created/i, '验证仅产生一次业务效果，第二次应为幂等返回'],
    [/repeat with the same request identifier/i, '使用相同的幂等键再次发送请求'],
    [/document and implement 401\/403 and 404/i, '应实现 401/403/404 等标准错误响应'],
    [/upload and ingest a document/i, '文档上传与解析'],
    [/save test environment configuration/i, '测试环境配置'],
    [/register enterprise tool connector/i, '企业工具连接器注册'],
    [/rebuild knowledge center/i, '知识库重建'],
    [/run autonomous bug scanning/i, '自主扫描引擎'],
    [/save llm configuration/i, 'LLM 配置保存'],
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

/** Generate practical testing steps based on bug type */
function getActionableSteps(f: Finding): string[] {
  const t = f.title.toLowerCase();
  const method = f.repro_method || 'GET';
  const path = f.repro_path || '';

  // Error contract / auth missing
  if (t.includes('401') || t.includes('403') || t.includes('lacks') || t.includes('error contract')) {
    return [
      `不带任何认证信息，直接请求 ${method} ${path}`,
      '观察返回的 HTTP 状态码和响应内容',
      '预期应返回 401 或 403，若返回 200 则确认缺陷',
    ];
  }

  // Idempotency
  if (t.includes('idempotenc') || t.includes('幂等') || t.includes('replay')) {
    return [
      `使用相同参数，连续两次请求 ${method} ${path}`,
      '对比两次请求的返回结果和数据库状态',
      '预期第二次应为幂等返回（409/200+相同结果），若创建了重复记录则确认缺陷',
    ];
  }

  // DB verified
  if (t.includes('db verified') || t.includes('库存') || t.includes('bom') || f.source_entity) {
    const entity = f.source_entity || '相关数据表';
    return [
      `在数据库中查询 ${entity} 表，检查数据一致性`,
      '对比 API 返回数据和数据库实际存储',
      '如发现不一致（如负库存、无效引用），追溯相关业务操作日志',
    ];
  }

  // Async / observable
  if (t.includes('async') || t.includes('observable') || t.includes('progress')) {
    return [
      `请求 ${method} ${path}，观察是否返回可追踪的进度标识`,
      '等待异步操作完成后，查询结果状态',
      '预期应能通过返回的 ID 查询到操作进度和最终结果',
    ];
  }

  // Spec structure / operationId
  if (t.includes('operationid') || t.includes('spec')) {
    return [
      `检查 ${path || '该接口'} 的 OpenAPI 定义`,
      '确认是否缺少 operationId 或必要的响应声明',
      '补充 operationId 和缺失的错误响应定义',
    ];
  }

  // Generic fallback: use API steps but make them practical
  if (path) {
    return [
      `使用 curl 或 API 工具请求 ${method} ${path}`,
      '观察响应状态码和返回内容',
      '对比 PRD 文档中的预期行为，确认是否存在偏差',
    ];
  }

  return ['参考上方复现命令和预期/实际对比进行验证'];
}

type FindingType = 'all' | 'P0' | 'P1' | 'P2' | 'API' | 'DB' | '业务';

export function Findings() {
  const [params] = useSearchParams();
  const project = params.get('project') || 'real_project_demo';
  const { findings, loading, error, refetch } = useFindingsData(project);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<FindingType>('all');

  const filters: Array<{ label: string; value: FindingType }> = [
    { label: `全部 (${findings.length})`, value: 'all' },
    { label: `API (${findings.filter(f => f.repro_path || f.title.toLowerCase().includes('api') || f.title.includes('接口')).length})`, value: 'API' },
    { label: `DB (${findings.filter(f => f.title.includes('DB Verified') || f.title.includes('库存') || f.title.includes('BOM') || f.title.includes('流水') || f.title.includes('数据库')).length})`, value: 'DB' },
    { label: `业务 (${findings.filter(f => !f.repro_path && !f.title.includes('DB Verified') && !f.title.includes('库存') && !f.title.includes('BOM') && !f.title.includes('数据库')).length})`, value: '业务' },
    { label: `P0`, value: 'P0' },
    { label: `P1`, value: 'P1' },
    { label: `P2`, value: 'P2' },
  ];

  const displayData = (() => {
    if (filter === 'all') return findings;
    if (filter === 'P0' || filter === 'P1' || filter === 'P2') return findings.filter(f => f.severity === filter);
    if (filter === 'API') return findings.filter(f => !!f.repro_path || f.title.toLowerCase().includes('api') || f.title.includes('接口'));
    if (filter === 'DB') return findings.filter(f => f.title.includes('DB Verified') || f.title.includes('库存') || f.title.includes('BOM') || f.title.includes('流水') || f.title.includes('数据库'));
    if (filter === '业务') return findings.filter(f => !f.repro_path && !f.title.includes('DB Verified') && !f.title.includes('库存') && !f.title.includes('BOM') && !f.title.includes('数据库'));
    return findings;
  })();

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>行为裂隙</h1>
          <p>系统行为的预期与实际偏差 · 全部可复现 · 证据链完整</p>
        </div>
        {error && (
          <button className="btn btn-secondary" onClick={refetch} style={{ fontSize: 11 }}>🔄 重试</button>
        )}
      </div>

      {/* Filters */}
      <div className="filters mb-4">
        {filters.map(f => (
          <button key={f.value} onClick={() => setFilter(f.value)}
            className={`filter${filter === f.value ? ' active' : ''}`}>{f.label}</button>
        ))}
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <div className="spinner" style={{ margin: '0 auto 16px' }} />
          <p style={{ color: 'var(--muted)', fontSize: 13 }}>加载风险数据...</p>
        </div>
      )}

      {/* Error with no data */}
      {!loading && error && displayData.length === 0 && (
        <div style={{ textAlign: 'center', padding: 60, background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
          <div style={{ fontSize: 36, marginBottom: 12 }}>📡</div>
          <p style={{ color: 'var(--danger)', fontWeight: 600, marginBottom: 8 }}>数据加载失败</p>
          <p style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 16 }}>{error}</p>
          <button className="btn btn-primary" onClick={refetch}>重试</button>
        </div>
      )}

      {/* Empty state — all data */}
      {!loading && !error && displayData.length === 0 && filter === 'all' && (
        <div style={{ textAlign: 'center', padding: 60, background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>🔍</div>
          <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>暂无风险发现</h3>
          <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 20 }}>系统尚未检测到行为偏差。运行一次扫描来分析您的系统。</p>
          <button className="btn btn-primary" onClick={() => window.location.href = `/dashboard?project=${project}`}>
            前往总览启动扫描
          </button>
        </div>
      )}

      {/* Empty state — filter has no matches */}
      {!loading && !error && displayData.length === 0 && filter !== 'all' && (
        <div style={{ textAlign: 'center', padding: 48, background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 'var(--radius)' }}>
          <div style={{ fontSize: 36, marginBottom: 12 }}>✅</div>
          <h3 style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>无 {filter === 'API' ? 'API 接口' : filter === 'DB' ? '数据库' : filter === '业务' ? '业务逻辑' : filter} 类型风险</h3>
          <p style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 16 }}>当前系统中没有该类型的风险发现。</p>
          <button className="btn btn-secondary" onClick={() => setFilter('all')} style={{ fontSize: 12 }}>← 查看全部</button>
        </div>
      )}

      {/* Findings List */}
      {displayData.map(f => {
        const isOpen = expandedId === f.id;
        const sevStyle = f.severity === 'P0'
          ? { bg: 'var(--danger-muted)', color: 'var(--danger)' }
          : f.severity === 'P1'
            ? { bg: 'var(--warning-muted)', color: 'var(--warning)' }
            : { bg: 'var(--primary-muted)', color: 'var(--primary)' };

        return (
          <div key={f.id} className={`evidence-item ${f.severity.toLowerCase()}${isOpen ? ' open' : ''}`}>
            <div className="evidence-head" onClick={() => setExpandedId(isOpen ? null : f.id)}>
              <span className={`severity ${f.severity.toLowerCase()}`}>{f.severity}</span>
              <span className="evidence-title">{f.title}</span>
              <span className="evidence-meta">
                <span>复现 {f.reproducibility_count} 次</span>
                <time>{f.timestamp}</time>
              </span>
              <span className="evidence-expand">{isOpen ? '▲' : '▼'}</span>
            </div>
            <div className="evidence-body" style={{ display: isOpen ? 'block' : 'none' }}>
              {/* Expected vs Actual */}
              <div className="grid grid-2 gap-4 mb-3" style={{ paddingTop: 14 }}>
                <div>
                  <span style={{ fontSize: 10, color: 'var(--muted)', fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.04em' }}>预期行为</span>
                  <p style={{ fontSize: 12, marginTop: 4, color: 'var(--ink)' }}>{formatText(f.expected) || '未指定'}</p>
                </div>
                <div>
                  <span style={{ fontSize: 10, color: 'var(--danger)', fontWeight: 800, textTransform: 'uppercase', letterSpacing: '.04em' }}>实际行为</span>
                  <p style={{ fontSize: 12, marginTop: 4, color: 'var(--danger)' }}>{formatText(f.actual) || '未捕获'}</p>
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

              {/* Reproduction */}
              {(f.repro_method && f.repro_path) && (
                <div style={{ marginTop: 14, padding: 14, background: '#1e293b', borderRadius: 8, fontFamily: 'monospace', fontSize: 11, color: '#e2e8f0', lineHeight: 1.7 }}>
                  <div style={{ color: '#94a3b8', marginBottom: 6, fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.05em' }}>🖥 复现命令</div>
                  <code style={{ color: '#22d3bb' }}>curl</code>
                  <code style={{ color: '#f8fafc' }}> -X {f.repro_method} </code>
                  <code style={{ color: '#fbbf24' }}>"{f.repro_path}"</code>
                  {f.repro_method !== 'GET' && (
                    <code style={{ color: '#94a3b8' }}> -H "Content-Type: application/json" -d '{'{...}'}'</code>
                  )}
                </div>
              )}

              {/* Repro steps */}
              {(() => {
                const steps = getActionableSteps(f);
                return (
                <div style={{ marginTop: 10, padding: 14, background: '#f8fafc', borderRadius: 8, border: '1px solid var(--line)' }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 8 }}>📋 验证步骤</div>
                  <ol style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: 'var(--ink)', lineHeight: 1.8 }}>
                    {steps.map((step, i) => (
                      <li key={i} style={{ marginBottom: 4 }}>{step}</li>
                    ))}
                  </ol>
                </div>
                );
              })()}

              {/* Investigation guide */}
              {(f.repro_path || f.source_entity) && (
                <div style={{ marginTop: 10, padding: 14, background: '#fefce8', borderRadius: 8, border: '1px solid #fde68a' }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: '#b35f09', textTransform: 'uppercase', letterSpacing: '.05em', marginBottom: 8 }}>🔎 排查指引</div>
                  <div style={{ fontSize: 12, color: 'var(--ink)', lineHeight: 1.8 }}>
                    {f.repro_path && (
                      <div>• 请求路径：<code style={{ background: '#fef3c7', padding: '1px 6px', borderRadius: 3, fontSize: 11 }}>{f.repro_method} {f.repro_path}</code></div>
                    )}
                    {f.source_entity && (
                      <div>• 涉及模块：{toChinese(f.source_entity)}</div>
                    )}
                    <div style={{ marginTop: 6, color: 'var(--muted)' }}>
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
                  <code>{f.proof.hash}</code>
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
