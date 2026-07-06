import { useState, lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router-dom';
import { hasRealReplayAsset, isCustomerReadyFinding, useFindingsData } from '../api/data';
import { usePageTitle } from '../lib/page-title';
import { formatBeijingDateTime } from '../lib/time';
import { useProjectNavigation } from '../lib/project-navigation';
import { EvidenceTimeline } from '../components/EvidenceTimeline';
import type { Finding } from '../types';

const ReplayViewer = lazy(() => import('../components/ReplayViewer'));

type FindingType = 'all' | 'P0' | 'P1' | 'P2' | string;

function buildReplayCommand(method: string, path: string, rawCommand?: string) {
  const command = String(rawCommand || '').trim();
  if (command) return command;
  return '';
}

function getFilterDisplayName(f: FindingType): string {
  if (f === 'all') return '全部';
  if (f === 'P0') return 'P0 严重缺陷';
  if (f === 'P1') return 'P1 一般缺陷';
  if (f === 'P2') return 'P2 轻微缺陷';
  if (f === 'quality_gap') return '内部诊断线索';  // 仅内部线索页使用，不在客户交付列表展示
  return f;
}

function getSeverityHeadline(severity: Finding['severity']) {
  if (severity === 'P0') return '立即处理';
  if (severity === 'P1') return '优先整改';
  return '纳入回归';
}

function getFindingModule(finding: Finding) {
  return String(finding.business_impact?.module || finding.source_entity || finding.defect_family_label || '核心业务').trim() || '核心业务';
}

function getFindingOwner(finding: Finding) {
  return String(finding.investigation_guidance?.primary_area || getFindingModule(finding)).trim() || '待分派';
}

export function Findings() {
  usePageTitle('行为验证');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const { findings, clues, loading, error, refetch } = useFindingsData(project);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<FindingType>('all');
  const [replayFinding, setReplayFinding] = useState<Finding | null>(null);
  const customerReadyFindings = findings.filter(isCustomerReadyFinding);
  const hiddenPendingCount = clues.length;

  const p0Count = customerReadyFindings.filter(f => f.severity === 'P0').length;
  const p1Count = customerReadyFindings.filter(f => f.severity === 'P1').length;
  const p2Count = customerReadyFindings.filter(f => f.severity === 'P2').length;
  const qualityGapCount = customerReadyFindings.filter(f => f.quality_assurance_gap).length;
  const validatedCount = customerReadyFindings.filter(f => f.evidence_quality?.level === 'validated').length;
  const topModules = Array.from(new Set(customerReadyFindings.map(getFindingModule))).slice(0, 3);

  // 按缺陷族聚合统计
  const familyStats = new Map<string, { label: string; count: number }>();
  for (const f of customerReadyFindings) {
    const family = f.defect_family || 'other';
    const label = f.defect_family_label || family;
    const existing = familyStats.get(family);
    if (existing) {
      existing.count++;
    } else {
      familyStats.set(family, { label, count: 1 });
    }
  }

  // 客户交付页面：只展示已复现缺陷。保障缺口移至内部线索页面。
  const filters: Array<{ label: string; value: FindingType }> = [
    { label: `全部 (${customerReadyFindings.length})`, value: 'all' },
    ...Array.from(familyStats.entries()).map(([family, meta]) => ({
      label: `${meta.label} (${meta.count})`,
      value: family,
    })),
    { label: `P0`, value: 'P0' },
    { label: `P1`, value: 'P1' },
    { label: `P2`, value: 'P2' },
  ];

  const displayData = (() => {
    if (filter === 'all') return customerReadyFindings;
    if (filter === 'P0' || filter === 'P1' || filter === 'P2') return customerReadyFindings.filter(f => f.severity === filter);
    if (filter === 'quality_gap') return customerReadyFindings.filter(f => f.quality_assurance_gap);
    return customerReadyFindings.filter(f => f.defect_family === filter);
  })();

  return (
    <div>
      <section className="customer-showcase findings-showcase mb-4">
        <div className="customer-showcase-main">
          <span className="panel-kicker">客户整改清单</span>
          <h1>
            {customerReadyFindings.length > 0
              ? `当前已确认 ${customerReadyFindings.length} 个可交付缺陷，可直接进入整改闭环。`
              : '当前没有可交付缺陷。'}
          </h1>
          <p>
            {customerReadyFindings.length > 0
              ? `本页只保留已验证、可复现、具备证据链的客户缺陷。当前重点集中在 ${topModules.length > 0 ? topModules.join('、') : '核心业务'} 等模块。`
              : hiddenPendingCount > 0
                ? `当前仅有 ${hiddenPendingCount} 条内部线索仍在补证，不建议对客户展示。待形成真实证据后，再进入本清单。`
                : '当前项目尚未形成 confirmed 缺陷，本页会在出现真实可交付问题后自动更新。'}
          </p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">可交付缺陷 {customerReadyFindings.length}</span>
            <span className="summary-pill">立即处理 {p0Count}</span>
            <span className="summary-pill">优先整改 {p1Count}</span>
            <span className="summary-pill">待补证线索 {hiddenPendingCount}</span>
          </div>
          <div className="customer-showcase-actions">
            <button className="btn btn-primary" onClick={() => navigateToProjectPath('/evidence', project)}>查看证据链</button>
            {hiddenPendingCount > 0 && (
              <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/clues', project)}>查看内部线索</button>
            )}
            {error && <button className="btn btn-secondary" onClick={refetch}>重新加载</button>}
          </div>
        </div>
        <div className="customer-showcase-side">
          <div className={`customer-status-card ${p0Count > 0 ? 'danger' : customerReadyFindings.length > 0 ? 'warning' : 'success'}`}>
            <span>整改建议</span>
            <strong>
              {p0Count > 0
                ? '先处理阻断项'
                : customerReadyFindings.length > 0
                  ? '带着清单推进整改'
                  : '当前无需客户闭环'}
            </strong>
            <p>
              {p0Count > 0
                ? `${p0Count} 个 P0 缺陷需要立即拉齐研发与业务负责人。`
                : customerReadyFindings.length > 0
                  ? `${validatedCount} 条缺陷已具备高质量证据，可直接进入复验。`
                  : '当前不要把待补证线索交给客户，以免稀释产品价值。'}
            </p>
          </div>
          <div className="customer-status-meta">
            <span><em>重点模块</em><b>{topModules.length > 0 ? topModules.join('、') : '暂无'}</b></span>
            <span><em>证据达标</em><b>{validatedCount}/{customerReadyFindings.length || 0}</b></span>
            <span><em>覆盖类型</em><b>{familyStats.size}</b></span>
          </div>
        </div>
      </section>

      {customerReadyFindings.length > 0 && (
        <div className="customer-summary-grid findings-summary-grid mb-4">
          {[
            { label: '立即处理', val: p0Count, tone: 'danger', note: p0Count > 0 ? '会直接影响发布或履约' : '当前无阻断项' },
            { label: '优先整改', val: p1Count, tone: 'warning', note: p1Count > 0 ? '建议本轮进入闭环' : '当前无高风险积压' },
            { label: '证据达标', val: validatedCount, tone: 'primary', note: '已满足客户复验与验收口径' },
            { label: '涉及模块', val: familyStats.size, tone: 'neutral', note: '已形成明确归类与分派方向' },
          ].map((item) => (
            <article key={item.label} className={`customer-summary-card tone-${item.tone}`}>
              <span>{item.label}</span>
              <strong>{item.val}</strong>
              <small>{item.note}</small>
            </article>
          ))}
        </div>
      )}

      {customerReadyFindings.length > 0 && (
        <div className="page-header findings-page-header">
          <div>
            <span className="panel-kicker">清单视图</span>
            <h1>行为验证</h1>
            <p>把可交付缺陷整理成客户可理解、研发可执行、测试可复验的整改项。</p>
          </div>
          <div className="findings-toolbar-note">
            当前展示 {filter === 'all' ? '全部' : getFilterDisplayName(filter)} 整改项
          </div>
        </div>
      )}

      {customerReadyFindings.length > 0 && (
        <div className="filters behavior-filters findings-filter-bar mb-4">
          {filters.map(f => (
            <button key={f.value} onClick={() => setFilter(f.value)} className={`filter${filter === f.value ? ' active' : ''}`}>{f.label}</button>
          ))}
        </div>
      )}

      {loading && (
        <div className="state-panel">
          <div className="spinner spinner-centered" />
          <p>正在整理可交付缺陷...</p>
        </div>
      )}

      {!loading && error && displayData.length === 0 && (
        <section className="findings-empty-state danger">
          <span className="findings-empty-kicker">连接异常</span>
          <h3>缺陷数据暂时不可用</h3>
          <p>{error}</p>
          <button className="btn btn-primary" onClick={refetch}>重新连接</button>
        </section>
      )}

      {!loading && !error && displayData.length === 0 && filter === 'all' && (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">当前结论</span>
          <h3>当前暂不向客户展示缺陷清单</h3>
          <p>{hiddenPendingCount > 0 ? `本轮扫描只产出了 ${hiddenPendingCount} 条待验证线索，尚不足以进入客户交付。请继续补采真实请求、日志或 DB 快照。` : '系统尚未检测到可交付的已验证缺陷。运行一次扫描后，这里会自动展示真实可交付问题。'}</p>
          <div className="findings-repro-actions">
            <button className="btn btn-primary" onClick={() => navigateToProjectPath('/dashboard', project)}>前往总览启动扫描</button>
            {hiddenPendingCount > 0 && (
              <button className="btn btn-secondary" onClick={() => navigateToProjectPath('/clues', project)}>查看待验证线索</button>
            )}
          </div>
        </section>
      )}

      {!loading && !error && displayData.length === 0 && filter !== 'all' && (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">筛选结果</span>
          <h3>当前没有对应风险</h3>
          <p>无 {getFilterDisplayName(filter)} 类型风险发现。</p>
          <button className="btn btn-secondary" onClick={() => setFilter('all')}>查看全部</button>
        </section>
      )}

      {!loading && !error && displayData.length > 0 && (
        <section className="customer-value-grid findings-agenda-grid mb-4">
          <article className="customer-value-card">
            <span className="customer-value-kicker">本页价值</span>
            <h2>客户直接看到已确认问题，不再看待补证噪音</h2>
            <p>当前清单里的每一项都具备证据链、业务影响和整改建议，可以直接进入客户沟通和修复排期。</p>
          </article>
          <article className="customer-value-card">
            <span className="customer-value-kicker">当前重点</span>
            <h2>{p0Count > 0 ? `优先处理 ${p0Count} 个阻断项` : `优先收敛 ${p1Count} 个高风险问题`}</h2>
            <p>{p0Count > 0 ? '先保障发布与核心履约，再处理一般缺陷。' : '当前没有阻断项，适合按模块和业务影响推进闭环。'} </p>
          </article>
          <article className="customer-value-card">
            <span className="customer-value-kicker">执行方式</span>
            <h2>每条卡片都给出影响、建议和复验入口</h2>
            <p>卡片首屏先看业务结论，展开后再看证据细节、复现命令和排查指引，避免客户被技术细节淹没。</p>
          </article>
        </section>
      )}

      {displayData.map(f => {
        const isOpen = expandedId === f.id;
        const repro = f.reproduction || {};
        const steps = (repro.steps && repro.steps.length > 0) ? repro.steps : [];
        const eq = f.evidence_quality || {};
        const inv = f.investigation_guidance || {};
        const replayCommand = buildReplayCommand(repro.method || '', repro.path || '', repro.curl_command);
        const hasRealReplay = hasRealReplayAsset(f) && Boolean(replayCommand);
        const moduleName = getFindingModule(f);
        const owner = getFindingOwner(f);
        const urgency = String(f.business_impact?.urgency || getSeverityHeadline(f.severity)).trim() || getSeverityHeadline(f.severity);
        const impactSummary = f.business_impact?.summary || f.business_summary || f.actual || '该问题已形成客户可交付缺陷。';
        const nextAction = f.recommended_fix || eq.next_actions?.[0] || '请按排查指引完成修复并回归验证。';
        const affectedCount = f.affected_count || f.affected_instances?.length || 0;

        return (
          <article key={f.id} className={`findings-delivery-card severity-${f.severity.toLowerCase()}${isOpen ? ' open' : ''}`}>
            <div className="findings-delivery-head" onClick={() => setExpandedId(isOpen ? null : f.id)}>
              <div className="findings-delivery-title">
                <div className="findings-delivery-badges">
                  <span className={`severity ${f.severity.toLowerCase()}`}>{f.severity}</span>
                  <span className="findings-delivery-badge">{urgency}</span>
                  <span className="findings-delivery-badge subtle">{moduleName}</span>
                </div>
                <h2>{f.title}</h2>
                <p>{impactSummary}</p>
              </div>
              <div className="findings-delivery-meta">
                <span><em>负责人建议</em><b>{owner}</b></span>
                <span><em>证据状态</em><b>{eq.label || '已归档'}</b></span>
                <span><em>复现稳定性</em><b>{f.proof?.repro_rate ?? 0}%</b></span>
                <button type="button" className="btn btn-secondary btn-sm" onClick={(e) => { e.stopPropagation(); setExpandedId(isOpen ? null : f.id); }}>
                  {isOpen ? '收起细节' : '查看细节'}
                </button>
              </div>
            </div>
            <div className="findings-delivery-strip">
              <div className="findings-delivery-strip-item">
                <span>业务影响</span>
                <strong>{impactSummary}</strong>
              </div>
              <div className="findings-delivery-strip-item">
                <span>整改建议</span>
                <strong>{nextAction}</strong>
              </div>
              <div className="findings-delivery-strip-item">
                <span>影响范围</span>
                <strong>{affectedCount > 0 ? `已命中 ${affectedCount} 个业务实例` : f.affected_scope || '待继续量化'}</strong>
              </div>
            </div>

            <div className="evidence-body">
              <div className="findings-compare-grid findings-delivery-grid">
                <div className="findings-compare-card">
                  <span className="findings-compare-label">客户看到的问题</span>
                  <p>{impactSummary}</p>
                </div>
                <div className="findings-compare-card">
                  <span className="findings-compare-label">建议谁来处理</span>
                  <p>{owner}</p>
                </div>
                <div className="findings-compare-card">
                  <span className="findings-compare-label">建议下一步</span>
                  <p>{nextAction}</p>
                </div>
                <div className="findings-compare-card danger">
                  <span className="findings-compare-label">预期 vs 实际</span>
                  <p>{f.expected_actual_comparison?.difference || `${f.expected || '未指定'} / ${f.actual || '未捕获'}`}</p>
                </div>
              </div>

              {isOpen && (
                <>
              <div className="findings-compare-grid">
                <div className="findings-compare-card">
                  <span className="findings-compare-label">预期行为</span>
                  <p>{f.expected || '未指定'}</p>
                </div>
                <div className="findings-compare-card danger">
                  <span className="findings-compare-label">实际行为</span>
                  <p>{f.actual || '未捕获'}</p>
                </div>
              </div>

              {(f.evidence_chain?.length > 0) && <EvidenceTimeline steps={f.evidence_chain} />}

              {(hasRealReplay || steps.length > 0) && (
                <div className={`findings-repro-grid${hasRealReplay ? ' dual' : ''}`}>
                  {hasRealReplay && (
                    <div className="findings-repro-panel code">
                      <div className="findings-command-head">
                        <div>
                          <div className="findings-panel-kicker">接口复现</div>
                          <div className="findings-command-subtitle">{repro.method} {repro.path}</div>
                        </div>
                        <CopyButton text={replayCommand} />
                      </div>
                      <pre className="findings-command-block"><code>{replayCommand}</code></pre>
                      <div className="findings-repro-actions">
                        <button className="btn btn-primary btn-sm" onClick={() => setReplayFinding(f)}>点击复现</button>
                        <div className="findings-panel-note">复制到终端或导入 Postman 即可执行</div>
                      </div>
                    </div>
                  )}
                  <div className="findings-repro-panel business">
                    <div className="findings-panel-kicker">前端操作复现</div>
                    {!repro.is_synthetic && steps.length > 0 ? (
                      <ol className="findings-steps">
                        {steps.map((step, i) => <li key={i}>{step}</li>)}
                      </ol>
                    ) : (
                      <div className="findings-panel-note">当前未沉淀真实前端复验轨迹，请先补齐真实操作录屏或执行轨迹。</div>
                    )}
                    {hasRealReplay && (
                      <div className="findings-panel-note strong">对应接口：{repro.method} {repro.path}</div>
                    )}
                  </div>
                </div>
              )}

              {(repro.path || f.source_entity || f.evidence_hint) && (
                <div className="findings-investigation-card">
                  <div className="findings-panel-kicker warning">排查指引</div>
                  <div className="findings-investigation-body">
                    {repro.path && <div>请求路径：<code>{repro.method} {repro.path}</code></div>}
                    {f.source_entity && <div>涉及模块：{f.source_entity}</div>}
                    {f.evidence_hint && <div>排查线索：{f.evidence_hint}</div>}
                    {inv.log_search && <div><code>{inv.log_search}</code></div>}
                    {inv.sql_verify && <div><code>{inv.sql_verify}</code></div>}
                  </div>
                </div>
              )}

              <div className="evidence-proof">
                <svg viewBox="0 0 24 24" width="16" height="16"><path d="M20 6 9 17l-5-5" /></svg>
                <div>
                  <strong>证据验证{eq.score >= 80 ? '通过' : '中'} · 复现率 {f.proof?.repro_rate ?? 0}%</strong>
                  <p>{eq.label} · {eq.score}/100 · {eq.summary}</p>
                </div>
              </div>
                </>
              )}
            </div>
          </article>
        );
      })}

      {replayFinding && (
        <Suspense fallback={<div className="replay-loading"><div className="spinner spinner-centered" /></div>}>
          <ReplayViewer projectId={project} finding={replayFinding} onClose={() => setReplayFinding(null)} />
        </Suspense>
      )}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <button
      type="button"
      className={`btn btn-secondary btn-sm findings-copy-btn${copied ? ' copied' : ''}`}
      onClick={(e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? '已复制' : '复制命令'}
    </button>
  );
}

export default Findings;
