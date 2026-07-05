import { useState, lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useFindingsData } from '../api/data';
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

  const normalizedMethod = String(method || '').trim().toUpperCase();
  const normalizedPath = String(path || '').trim();
  if (!normalizedMethod || !normalizedPath) return '';

  return `curl -X ${normalizedMethod} "${normalizedPath}"`;
}

function getFilterDisplayName(f: FindingType): string {
  if (f === 'all') return '全部';
  if (f === 'P0') return 'P0 严重缺陷';
  if (f === 'P1') return 'P1 一般缺陷';
  if (f === 'P2') return 'P2 轻微缺陷';
  if (f === 'quality_gap') return '质量保障缺口';
  return f;
}

export function Findings() {
  usePageTitle('行为验证');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const { findings, loading, error, refetch } = useFindingsData(project);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<FindingType>('all');
  const [replayFinding, setReplayFinding] = useState<Finding | null>(null);

  const p0Count = findings.filter(f => f.severity === 'P0').length;
  const p1Count = findings.filter(f => f.severity === 'P1').length;
  const p2Count = findings.filter(f => f.severity === 'P2').length;
  const qualityGapCount = findings.filter(f => f.quality_assurance_gap).length;

  // 按缺陷族聚合统计
  const familyStats = new Map<string, { label: string; count: number }>();
  for (const f of findings) {
    const family = f.defect_family || 'other';
    const label = f.defect_family_label || family;
    const existing = familyStats.get(family);
    if (existing) {
      existing.count++;
    } else {
      familyStats.set(family, { label, count: 1 });
    }
  }

  const filters: Array<{ label: string; value: FindingType }> = [
    { label: `全部 (${findings.length})`, value: 'all' },
    ...Array.from(familyStats.entries()).map(([family, meta]) => ({
      label: `${meta.label} (${meta.count})`,
      value: family,
    })),
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
            <span className="summary-pill">已覆盖类型 {familyStats.size}</span>
            <span className="summary-pill">保障缺口 {qualityGapCount}</span>
          </div>
        </div>
        {error && <button className="btn btn-secondary" onClick={refetch}>重新加载</button>}
      </div>

      <div className="findings-stat-grid mb-4">
        {[
          { label: 'P0 阻断', value: p0Count, note: '需要立即闭环', tone: 'danger' },
          { label: 'P1 高风险', value: p1Count, note: '影响发布与履约', tone: 'warning' },
          { label: 'P2 提示', value: p2Count, note: '建议纳入回归', tone: 'primary' },
          { label: '已覆盖类型', value: familyStats.size, note: '已命中全谱分类维度', tone: 'neutral' },
        ].map((item) => (
          <article key={item.label} className={`findings-stat-card tone-${item.tone}`}>
            <strong>{item.value}</strong>
            <span>{item.label}</span>
            <small>{item.note}</small>
          </article>
        ))}
      </div>

      <div className="filters behavior-filters mb-4">
        {filters.map(f => (
          <button key={f.value} onClick={() => setFilter(f.value)} className={`filter${filter === f.value ? ' active' : ''}`}>{f.label}</button>
        ))}
      </div>

      {loading && (
        <div className="state-panel">
          <div className="spinner spinner-centered" />
          <p>正在整理风险清单...</p>
        </div>
      )}

      {!loading && error && displayData.length === 0 && (
        <section className="findings-empty-state danger">
          <span className="findings-empty-kicker">连接异常</span>
          <h3>风险数据暂时不可用</h3>
          <p>{error}</p>
          <button className="btn btn-primary" onClick={refetch}>重新连接</button>
        </section>
      )}

      {!loading && !error && displayData.length === 0 && filter === 'all' && (
        <section className="findings-empty-state">
          <span className="findings-empty-kicker">当前空态</span>
          <h3>暂无风险发现</h3>
          <p>系统尚未检测到行为偏差。运行一次扫描后，这里会自动切换为真实风险清单。</p>
          <button className="btn btn-primary" onClick={() => navigateToProjectPath('/dashboard', project)}>前往总览启动扫描</button>
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

      {displayData.map(f => {
        const isOpen = expandedId === f.id;
        const repro = f.reproduction || {};
        const steps = (repro.steps && repro.steps.length > 0) ? repro.steps : [];
        const eq = f.evidence_quality || {};
        const inv = f.investigation_guidance || {};
        const replayCommand = buildReplayCommand(repro.method || '', repro.path || '', repro.curl_command);

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

              {((repro.method && repro.path) || steps.length > 0) && (
                <div className={`findings-repro-grid${repro.method && repro.path ? ' dual' : ''}`}>
                  {repro.method && repro.path && (
                    <div className="findings-repro-panel code">
                      <div className="findings-command-head">
                        <div>
                          <div className="findings-panel-kicker">接口复现</div>
                          <div className="findings-command-subtitle">{repro.method} {repro.path}</div>
                        </div>
                        {replayCommand ? <CopyButton text={replayCommand} /> : null}
                      </div>
                      {replayCommand ? (
                        <pre className="findings-command-block"><code>{replayCommand}</code></pre>
                      ) : (
                        <div className="findings-panel-note">当前缺少可执行命令，请先补齐真实运行结果。</div>
                      )}
                      <div className="findings-repro-actions">
                        <button className="btn btn-primary btn-sm" onClick={() => setReplayFinding(f)}>点击复现</button>
                        <div className="findings-panel-note">复制到终端或导入 Postman 即可执行</div>
                      </div>
                    </div>
                  )}
                  <div className="findings-repro-panel business">
                    <div className="findings-panel-kicker">前端操作复现</div>
                    <ol className="findings-steps">
                      {steps.map((step, i) => <li key={i}>{step}</li>)}
                    </ol>
                    {repro.method && (
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
            </div>
          </div>
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
