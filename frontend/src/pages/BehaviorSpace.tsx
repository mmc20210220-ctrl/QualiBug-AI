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

type BehaviorAccumulator = BehaviorItem & {
  riskKeys: Set<string>;
};

const DISPLAYABLE_BUG_STATUS = new Set(['reproduced', 'suspected']);
const NON_ACTIONABLE_BUG_STATUS = new Set(['risk_clue', 'not_reproduced', 'false_positive']);
const DISPLAYABLE_VERDICT = new Set(['confirmed', 'validated_candidate']);
const SAMPLE_VALUE_RE = /(?:^|[-_=:/])(?:sample|mock|demo|draft|placeholder|example|test)(?:$|[-_=:/])/i;
const PLACEHOLDER_PATH_RE = /(?:\{[^}/]+\}|:[a-z_][a-z0-9_]*\b|QUALIBUG_UNRESOLVED_ID|<\s*(?:FILL|TODO|REQUIRED|SANDBOX|REPLACE)[^>]*>)/i;
const BEHAVIOR_SUBJECT_RE = /^([a-z][a-z0-9_-]{1,40})(?=[:：\s]|[\u0080-\uFFFF])/i;
const NON_BUSINESS_SUBJECTS = new Set(['api', 'cors', 'get', 'http', 'patch', 'post', 'put', 'sec', 'ui']);
const AGGREGATE_LAYER_FINDING_RE = /^\d+\s*个?.{0,24}层发现/;

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
  const riskBehaviors = useMemo(() => behaviors.filter((behavior) => behavior.findings > 0), [behaviors]);
  const maxFindings = Math.max(1, ...behaviors.map((behavior) => behavior.findings));
  const covered = behaviors.filter(r => r.tested).length;
  const zeroRiskCovered = behaviors.filter((behavior) => behavior.tested && behavior.findings === 0).length;
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
          <div className="coverage-header-meta">
            仅展示有可行动风险的行为单元；{zeroRiskCovered} 个已触达且无风险的单元已归入覆盖汇总
          </div>
        </div>
        {riskBehaviors.length > 0 && (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead><tr><th>类型</th><th>标识</th><th>详情</th><th className="text-center">覆盖状态</th><th className="text-center">风险</th><th>风险热度</th></tr></thead>
            <tbody>
              {riskBehaviors.map((b, i) => (
                <tr key={`${b.type}-${b.identifier}-${i}`}>
                  <td><span className={`behavior-type-chip ${typeToneClass[b.type]}`}>{b.type}</span></td>
                  <td className="font-mono behavior-matrix-code">{formatBehaviorIdentifier(b.identifier)}</td>
                  <td className="behavior-matrix-detail">{formatBehaviorDetail(b.detail)}</td>
                  <td className="text-center">
                    <span className={`status ${b.findings > 0 ? 'status-warning' : b.tested ? 'status-success' : 'status-warning'}`}>{b.findings > 0 ? '有风险' : b.tested ? '无风险' : '待检测'}</span>
                  </td>
                  <td className={`text-center behavior-findings${b.findings > 0 ? ' is-risk' : ''}`}>{b.findings}</td>
                  <td>
                    <div className="behavior-heat-track">
                      <div
                        className={`behavior-heat-bar ${getBehaviorHeatTone(b.findings, maxFindings)}`}
                        style={{ '--heat-width': `${getBehaviorHeatWidth(b.findings, maxFindings)}%` } as CSSProperties}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
        {!loading && behaviors.length > 0 && riskBehaviors.length === 0 && (
          <section className="findings-empty-state compact">
            <span className="findings-empty-kicker">当前无可行动风险</span>
            <h3>已触达行为单元未发现可交付风险</h3>
            <p>当前矩阵只展示能支撑交付的风险证据链；无风险覆盖已计入上方覆盖汇总，不在明细表铺开。</p>
          </section>
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
  const byKey = new Map<string, BehaviorAccumulator>();

  findings.forEach((finding) => {
    if (!isDisplayableBehaviorUnit(finding)) return;
    const type = classifyBehaviorType(finding);
    const identifier = buildIdentifier(type, finding);
    if (!identifier) return;
    const key = `${type}:${identifier}`;
    const current = byKey.get(key);
    const riskKey = buildActionableRiskKey(finding);
    if (current) {
      if (riskKey) current.riskKeys.add(riskKey);
      current.findings = current.riskKeys.size;
      current.tested = current.tested || isCoveredFinding(finding);
      return;
    }
    const riskKeys = new Set<string>();
    if (riskKey) riskKeys.add(riskKey);
    byKey.set(key, {
      type,
      identifier,
      detail: buildDetail(type, finding),
      tested: isCoveredFinding(finding),
      findings: riskKeys.size,
      riskKeys,
    });
  });

  return Array.from(byKey.values())
    .map((item) => ({
      type: item.type,
      identifier: item.identifier,
      detail: item.detail,
      tested: item.tested,
      findings: item.findings,
    }))
    .sort((a, b) => b.findings - a.findings || Number(b.tested) - Number(a.tested) || a.type.localeCompare(b.type) || a.identifier.localeCompare(b.identifier));
}

/** Check if a path looks like a valid API endpoint (not a description text). */
function isValidApiPath(path: string): boolean {
  if (!path || typeof path !== 'string') return false;
  const p = path.trim();
  if (!p.startsWith('/')) return false;
  if (PLACEHOLDER_PATH_RE.test(p)) return false;
  // Must be ASCII-only (no Chinese chars that indicate description text)
  if (!/^[a-zA-Z0-9_/{}:.@.-]+$/.test(p)) return false;
  // Must have at least one alphabetic segment
  const segments = p.split('/').filter(Boolean);
  if (!segments.some(s => /^[a-zA-Z]/.test(s))) return false;
  return true;
}

function isDisplayableBehaviorUnit(finding: Finding): boolean {
  if (isValidApiPath(finding.repro_path)) return true;
  return Boolean(
    cleanEntityLabel(finding.source_entity)
    || cleanEntityLabel(finding.defect_family_label)
    || cleanEntityLabel(finding.reporting_bucket_label)
  );
}

function isCoveredFinding(finding: Finding): boolean {
  return hasRuntimeEvidence(finding);
}

function buildActionableRiskKey(finding: Finding): string {
  if (NON_ACTIONABLE_BUG_STATUS.has(String(finding.bug_status || ''))) return '';
  if (!hasRuntimeEvidence(finding)) return '';
  if (AGGREGATE_LAYER_FINDING_RE.test(String(finding.title || '').trim())) return '';
  const subject = extractBehaviorSubject(finding);
  const isApiBucket = isValidApiPath(finding.repro_path) && !subject;
  const stableParts = [
    finding.severity,
    finding.bug_status,
    finding.verdict,
    finding.defect_family || finding.reporting_bucket || finding.risk_type,
    finding.reporting_bucket,
    finding.risk_type,
  ];
  if (isApiBucket) {
    return [
      ...stableParts,
      finding.repro_method,
      finding.repro_path,
    ].filter(Boolean).join('|');
  }
  return [
    ...stableParts,
    normalizeRiskText(finding.title),
    normalizeRiskText(finding.expected),
    normalizeRiskText(finding.actual),
  ].filter(Boolean).join('|');
}

function hasRuntimeEvidence(finding: Finding): boolean {
  if (finding.gate_passed || finding.is_reproducible) return true;
  if (finding.raw_evidence?.has_real_evidence) return true;
  if (finding.evidence_quality?.can_reproduce) return true;
  if (finding.evidence_quality?.level === 'validated') return true;
  if (DISPLAYABLE_BUG_STATUS.has(String(finding.bug_status || ''))) return true;
  return DISPLAYABLE_VERDICT.has(String(finding.verdict || ''));
}

function cleanEntityLabel(value: string) {
  const normalized = String(value || '').trim();
  if (!normalized) return '';
  if (normalized.length > 80) return '';
  if (SAMPLE_VALUE_RE.test(normalized)) return '';
  if (/=/.test(normalized)) return '';
  if (/^[A-Z]{2,}[-_][A-Z0-9_-]+$/i.test(normalized)) return '';
  return normalized;
}

function extractBehaviorSubject(finding: Finding) {
  const candidates = [finding.title, finding.actual];
  for (const candidate of candidates) {
    const normalized = String(candidate || '').trim();
    const match = normalized.match(BEHAVIOR_SUBJECT_RE);
    const subject = cleanEntityLabel(match?.[1] || '');
    if (!subject) continue;
    const lower = subject.toLowerCase();
    if (NON_BUSINESS_SUBJECTS.has(lower)) continue;
    if (subject === subject.toUpperCase()) continue;
    return subject;
  }
  return '';
}

function normalizeRiskText(value: string) {
  return String(value || '')
    .trim()
    .replace(/'[^']*'|"[^"]*"/g, '<value>')
    .replace(/\b[0-9a-f]{8}-[0-9a-f-]{27,}\b/gi, '<id>')
    .replace(/\b[A-Z]{2,}[-_][A-Z0-9_-]+\b/gi, '<id>')
    .replace(/\b\d{6,}\b/g, '<number>')
    .replace(/\s+/g, ' ')
    .slice(0, 180);
}

function classifyBehaviorType(finding: Finding): BehaviorType {
  if (extractBehaviorSubject(finding)) return '业务流程';
  const source = `${finding.source_entity || ''} ${finding.title}`.toLowerCase();
  // Only classify as API if repro_path is a valid API endpoint
  if (isValidApiPath(finding.repro_path)) return 'API';
  if (source.includes('prd') || source.includes('openapi') || source.includes('文档')) return '文档';
  if (source.includes('db') || source.includes('table') || source.includes('transaction')) return '数据库';
  return '业务流程';
}

function buildIdentifier(type: BehaviorType, finding: Finding): string {
  if (type === 'API') {
    // Only use repro_path if it's valid; otherwise use a generic label
    // (don't fallback to title — title is a description, not an identifier)
    return isValidApiPath(finding.repro_path) ? finding.repro_path : '业务场景';
  }
  return extractBehaviorSubject(finding) || cleanEntityLabel(finding.source_entity) || cleanEntityLabel(finding.defect_family_label) || cleanEntityLabel(finding.reporting_bucket_label) || '';
}

function buildDetail(type: BehaviorType, finding: Finding): string {
  if (type === 'API') return finding.repro_method || 'GET';
  if (type === '业务流程') return finding.defect_family_label || finding.reporting_bucket_label || '运行证据聚合';
  if (type === '数据库') return finding.investigation_guidance?.relevant_tables?.join(', ') || '数据一致性证据';
  if (type === '文档') return finding.doc_refs?.[0]?.display_name || '资料规则证据';
  return '待补充';
}

function formatBehaviorIdentifier(value: string) {
  const normalized = String(value || '').trim();
  if (!normalized) return '待生成';
  if (normalized.toLowerCase() === 'system') return '系统主流程';
  if (normalized.toLowerCase() === 'default') return '默认业务链路';
  return normalized;
}

function formatBehaviorDetail(value: string) {
  const normalized = String(value || '').trim();
  if (!normalized) return '待补充';
  if (normalized.toLowerCase() === 'system') return '系统级行为信号';
  return normalized;
}

function getBehaviorHeatWidth(findings: number, maxFindings: number) {
  if (findings <= 0) return 0;
  return Math.max(8, Math.round((findings / Math.max(1, maxFindings)) * 100));
}

function getBehaviorHeatTone(findings: number, maxFindings: number) {
  const ratio = findings / Math.max(1, maxFindings);
  if (ratio >= 0.65 && findings >= 3) return 'tone-danger';
  if (ratio >= 0.25 || findings >= 2) return 'tone-warning';
  return 'tone-success';
}

export default BehaviorSpace;
