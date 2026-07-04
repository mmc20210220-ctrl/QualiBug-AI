import { useMemo, type CSSProperties } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useFindingsData } from '../api/data';
import { usePageTitle } from '../lib/page-title';
import type { Finding } from '../types';

type BehaviorType = 'API' | '数据库' | '文档' | '业务流程';

interface BehaviorItem {
  type: BehaviorType;
  identifier: string;    // API path, table name, doc section, process step
  detail: string;        // method, column, source file, trigger
  tested: boolean;
  findings: number;
}

const typeAccentMap: Record<BehaviorType, string> = {
  'API': 'var(--primary)',
  '数据库': 'var(--success)',
  '文档': 'var(--warning)',
  '业务流程': 'var(--danger)',
};

const typeToneClass: Record<BehaviorType, string> = {
  'API': 'tone-api',
  '数据库': 'tone-db',
  '文档': 'tone-doc',
  '业务流程': 'tone-flow',
};

export function BehaviorSpace() {
  usePageTitle('行为空间');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { findings, loading } = useFindingsData(project);
  const behaviors = useMemo(() => buildBehaviorItems(findings), [findings]);
  const covered = behaviors.filter(r => r.tested).length;
  const totalFindings = behaviors.reduce((s, r) => s + r.findings, 0);
  const pct = behaviors.length > 0 ? Math.round((covered / behaviors.length) * 100) : 0;
  const pending = Math.max(0, behaviors.length - covered);
  const typeSummary = (['API', '数据库', '文档', '业务流程'] as BehaviorType[]).map((type) => {
    const items = behaviors.filter((behavior) => behavior.type === type);
    return {
      type,
      count: items.length,
      findings: items.reduce((sum, behavior) => sum + behavior.findings, 0),
      covered: items.filter((behavior) => behavior.tested).length,
      coverage: items.length > 0 ? Math.round((items.filter((behavior) => behavior.tested).length / items.length) * 100) : 0,
      accent: typeAccentMap[type],
    };
  });
  const highestRisk = behaviors.reduce<BehaviorItem | null>((highest, current) => {
    if (!highest || current.findings > highest.findings) return current;
    return highest;
  }, null);
  const highestRiskLabel = highestRisk ? formatBehaviorIdentifier(highestRisk.identifier) : '待生成';
  const summaryCopy = behaviors.length > 0
    ? pending === 0
      ? `当前行为空间已形成完整覆盖，但仍需围绕 ${highestRiskLabel} 持续复核高风险链路，并结合发布门禁推进闭环。`
      : `当前还有 ${pending} 个行为点待进入检测闭环，${highestRisk ? `当前优先关注 ${highestRiskLabel}` : '当前没有明显风险聚焦点'}。`
    : project
      ? '当前项目还没有生成行为路径计划，完成一次真实扫描后这里会自动形成覆盖图。'
      : '请选择客户项目后查看行为空间覆盖结果。';

  return (
    <div>
      <div className="page-header">
        <div>
          <span className="panel-kicker">覆盖驾驶舱</span>
          <h1>行为空间</h1>
          <p>把真实检测结果映射成行为单元覆盖图，先判断覆盖广度，再判断剩余盲区与高风险聚集区。</p>
          <div className="page-summary-strip">
            <span className="summary-pill strong">覆盖率 {pct}%</span>
            <span className="summary-pill">行为点 {behaviors.length}</span>
            <span className="summary-pill">待检测 {pending}</span>
            <span className="summary-pill">风险发现 {totalFindings}</span>
          </div>
        </div>
      </div>
      {loading && (
        <section className="findings-empty-state compact">
          <div className="spinner spinner-centered" />
          <p>正在构建行为空间...</p>
        </section>
      )}

      <section className="behavior-space-hero mb-4">
        <div className="behavior-space-hero-main">
          <span className="behavior-space-kicker">覆盖结论</span>
          <h2>{behaviors.length > 0 ? `当前已覆盖 ${covered} / ${behaviors.length} 个行为单元` : '等待生成行为路径'}</h2>
          <p>{summaryCopy}</p>
        </div>
        <div className="behavior-space-hero-side">
          <div className="behavior-space-ring">
            <strong>{pct}%</strong>
            <span>已覆盖比例</span>
          </div>
          <div className="behavior-space-hero-meta">
            <div>
              <span>高风险聚焦</span>
              <strong>{highestRiskLabel}</strong>
            </div>
            <div>
              <span>待处理盲区</span>
              <strong>{pending}</strong>
            </div>
          </div>
        </div>
      </section>

      <div className="behavior-stat-grid mb-4">
        {[
          { label: '行为点总数', val: behaviors.length },
          { label: '已覆盖', val: covered },
          { label: '覆盖率', val: `${pct}%` },
          { label: '风险发现', val: totalFindings },
          { label: '待检测', val: pending },
        ].map(m => (
          <article key={m.label} className="behavior-stat-card">
            <strong>{m.val}</strong>
            <span>{m.label}</span>
          </article>
        ))}
      </div>

      <div className="behavior-type-grid mb-4">
        {typeSummary.map((item) => (
          <article
            key={item.type}
            className="behavior-type-card"
            style={{ '--accent': item.accent, '--coverage': `${item.coverage}%` } as CSSProperties}
          >
            <span className={`behavior-type-chip ${typeToneClass[item.type]}`}>{item.type}</span>
            <strong>{item.count}</strong>
            <div className="behavior-type-meta">
              <span>风险 {item.findings}</span>
              <span>覆盖 {item.covered}/{item.count || 0}</span>
            </div>
            <div className="behavior-type-track">
              <div className="behavior-type-bar" />
            </div>
            <small>{item.count > 0 ? `该类行为当前覆盖 ${item.coverage}%` : '当前没有识别到该类行为单元'}</small>
          </article>
        ))}
      </div>

      <div className="behavior-matrix-panel">
        <div className="behavior-matrix-head">
          <div>
            <span className="panel-kicker">矩阵明细</span>
            <h2>行为覆盖矩阵</h2>
          </div>
          <div className="coverage-header-meta">按行为单元查看覆盖状态、风险数量与优先补测点</div>
        </div>
        {behaviors.length > 0 && (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead><tr><th>类型</th><th>标识</th><th>详情</th><th className="text-center">覆盖状态</th><th className="text-center">风险</th><th>风险热度</th></tr></thead>
            <tbody>
              {behaviors.map((b, i) => (
                <tr key={`${b.type}-${b.identifier}-${i}`}>
                  <td><span className={`behavior-type-chip ${typeToneClass[b.type]}`}>{b.type}</span></td>
                  <td className="font-mono behavior-matrix-code">{formatBehaviorIdentifier(b.identifier)}</td>
                  <td className="behavior-matrix-detail">{formatBehaviorDetail(b.detail)}</td>
                  <td className="text-center">
                    <span className={`status ${b.tested ? 'status-success' : 'status-warning'}`}>{b.tested ? '已覆盖' : '待检测'}</span>
                  </td>
                  <td className={`text-center behavior-findings${b.findings > 0 ? ' is-risk' : ''}`}>{b.findings || '-'}</td>
                  <td>
                    <div className="behavior-heat-track">
                      <div
                        className={`behavior-heat-bar ${getBehaviorHeatTone(b.findings)}`}
                        style={{ '--heat-width': `${Math.min(100, (b.findings + 1) * 25)}%` } as CSSProperties}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
        {!loading && behaviors.length === 0 && (
          <section className="findings-empty-state compact">
            <span className="findings-empty-kicker">当前空态</span>
            <h3>暂未形成行为空间</h3>
            <p>{project ? '当前项目还没有生成行为路径计划。完成一次真实扫描后，这里会自动形成覆盖矩阵。' : '未选择项目，暂无行为空间数据。'}</p>
          </section>
        )}
      </div>
    </div>
  );
}

function buildBehaviorItems(findings: Finding[]): BehaviorItem[] {
  const byKey = new Map<string, BehaviorItem>();

  findings.forEach((finding) => {
    const type = classifyBehaviorType(finding);
    const identifier = buildIdentifier(type, finding);
    if (!identifier) return;
    const key = `${type}:${identifier}`;
    const current = byKey.get(key);
    if (current) {
      current.findings += 1;
      current.tested = current.tested || finding.verdict !== 'inconclusive';
      return;
    }
    byKey.set(key, {
      type,
      identifier,
      detail: buildDetail(type, finding),
      tested: finding.verdict !== 'inconclusive',
      findings: 1,
    });
  });

  return Array.from(byKey.values());
}

function classifyBehaviorType(finding: Finding): BehaviorType {
  const source = `${finding.source_entity || ''} ${finding.source_value || ''} ${finding.title}`.toLowerCase();
  if (finding.repro_path) return 'API';
  if (source.includes('prd') || source.includes('openapi') || source.includes('文档')) return '文档';
  if (source.includes('db') || source.includes('table') || source.includes('库存') || source.includes('transaction')) return '数据库';
  return '业务流程';
}

function buildIdentifier(type: BehaviorType, finding: Finding): string {
  if (type === 'API') return finding.repro_path || finding.title;
  return finding.source_entity || finding.title;
}

function buildDetail(type: BehaviorType, finding: Finding): string {
  if (type === 'API') return finding.repro_method || 'GET';
  return finding.source_value || finding.actual || finding.expected || '待补充';
}

function formatBehaviorIdentifier(value: string) {
  const normalized = String(value || '').trim();
  if (!normalized) return '待生成';
  if (normalized.toLowerCase() === 'system') return '系统主流程';
  if (normalized === 'RequiredFieldOracle') return '必填字段校验链路';
  if (normalized === 'IdempotencyOracle') return '幂等校验链路';
  if (normalized.toLowerCase() === 'default') return '默认业务链路';
  // Generic: return the normalized value as-is (no industry mapping)
  return normalized;
}

function formatBehaviorDetail(value: string) {
  const normalized = String(value || '').trim();
  if (!normalized) return '待补充';
  if (normalized.toLowerCase() === 'system') return '系统级行为信号';
  if (normalized === 'RequiredFieldOracle') return '必填字段校验规则';
  if (normalized === 'IdempotencyOracle') return '幂等约束校验规则';
  return normalized
    .replace(/RequiredFieldOracle/g, '必填字段校验规则')
    .replace(/IdempotencyOracle/g, '幂等约束校验规则')
    .replace(/\bV12\b/g, '当前检测链路');
}

function getBehaviorHeatTone(findings: number) {
  if (findings >= 2) return 'tone-danger';
  if (findings === 1) return 'tone-warning';
  return 'tone-success';
}

export default BehaviorSpace;
