import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  getTestIntelligence,
  type TestCoverageStatus,
  type TestEvidence,
  type TestIntelligenceAnalysis,
  type TestObligation,
  type TestObligationKind,
} from '../api/test-intelligence';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import './TestIntelligence.css';

const KIND_META: Record<TestObligationKind, { label: string; short: string; description: string }> = {
  business_rule: { label: '业务规则', short: '规则', description: '验证来源明确声明的业务约束与模态' },
  lifecycle_transition: { label: '状态流转', short: '状态', description: '验证已定义生命周期中的允许或禁止流转' },
  authorization: { label: '权限边界', short: '权限', description: '验证角色、操作与业务对象的授权决策' },
  side_effect: { label: '业务副作用', short: '副作用', description: '验证后置条件、数据变化与补偿结果' },
  requirement_risk: { label: '需求风险', short: '风险', description: '规划中的 Requirement Finding 联动义务' },
};

const COVERAGE_META: Record<TestCoverageStatus, { eyebrow: string; title: string; description: string }> = {
  COVERED: {
    eyebrow: '支持语义覆盖 · 已形成义务',
    title: '当前支持的业务语义均已形成证据化 Test Obligation',
    description: '这里的 Covered 只覆盖 Test Intelligence v1 已支持的正式业务语义，不代表总测试完整率，也不代表任何测试已经执行。',
  },
  PARTIAL: {
    eyebrow: '支持语义覆盖 · 部分覆盖',
    title: '部分已理解业务语义尚未形成可交付测试义务',
    description: '系统只提升有完整来源证据且满足当前语义边界的 Test Obligation；缺口会保留为未覆盖语义，而不是自动补写测试。',
  },
  NOT_MEASURED: {
    eyebrow: '支持语义覆盖 · 暂不可度量',
    title: '当前还没有可度量的支持语义',
    description: '这不是 100% 覆盖。当前企业理解结果里还没有进入 v1 denominator 的正式业务语义，系统不会为了填满报告而制造测试义务。',
  },
};

function evidenceAnchor(evidence: TestEvidence): string {
  return evidence.sourceLocator || evidence.assetRef || evidence.documentBlockId || evidence.documentNodeId || evidence.factId;
}

function displayValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (!value || typeof value !== 'object') return '';
  const row = value as Record<string, unknown>;
  const kind = String(row.kind || '').trim();
  if (kind === 'state') return `${String(row.object_ref || '业务对象')} 状态 = ${String(row.state || '未定义')}`;
  if (kind === 'authorization_decision') return `授权决策：${String(row.decision || '未定义')}`;
  if (kind === 'lifecycle_transition') return `${String(row.from_state || '?')} → ${String(row.to_state || '?')} · ${String(row.transition_kind || '未定义')}`;
  if (kind === 'business_modality') return `业务模态：${String(row.modality || '未定义')}`;
  if (kind === 'postcondition') return `后置条件：${String(row.statement || '')}`;
  if (kind === 'data_effect') {
    const target = [row.object, row.field].map((item) => String(item || '').trim()).filter(Boolean).join(' / ');
    return `数据变化${target ? `（${target}）` : ''}：${String(row.statement || '')}`;
  }
  if (kind === 'compensation') return `补偿结果：${String(row.statement || '')}`;
  const field = String(row.field_candidate || '').trim();
  const operator = String(row.operator_candidate || '').trim();
  const raw = String(row.raw_value || '').trim();
  if (field || operator || raw) return [field, operator, raw].filter(Boolean).join(' ');
  return JSON.stringify(row) || '';
}

function EvidenceList({ evidence }: { evidence: TestEvidence[] }) {
  return (
    <div className="ti-evidence-list" aria-label="来源证据">
      {evidence.map((item, index) => (
        <article className="ti-evidence" key={`${item.sourceId}:${evidenceAnchor(item)}:${index}`}>
          <div className="ti-evidence-head">
            <strong>{item.sourceId || '企业资料'}</strong>
            {evidenceAnchor(item) && <code>{evidenceAnchor(item)}</code>}
          </div>
          {item.quote && <blockquote>{item.quote}</blockquote>}
          <div className="ti-evidence-meta">
            {item.factId && <span>Fact: {item.factId}</span>}
            {item.derivation && <span>{item.derivation}</span>}
          </div>
        </article>
      ))}
    </div>
  );
}

function ObligationCard({ obligation }: { obligation: TestObligation }) {
  const meta = KIND_META[obligation.obligationKind];
  const preconditions = obligation.preconditions.map(displayValue).filter(Boolean);
  const outcomes = obligation.expectedOutcomes.map(displayValue).filter(Boolean);
  return (
    <article className={`ti-obligation kind-${obligation.obligationKind}`}>
      <div className="ti-obligation-head">
        <div>
          <div className="ti-tags">
            <span className={`ti-tag kind-${obligation.obligationKind}`}>{meta.label}</span>
            <span className="ti-tag neutral">仅义务</span>
            <span className="ti-tag neutral">未执行</span>
          </div>
          <h3>{obligation.title}</h3>
          <p>{obligation.objective}</p>
        </div>
        <span className="ti-obligation-id" title={obligation.obligationId}>{obligation.obligationId}</span>
      </div>

      <div className="ti-context-grid">
        <div><span>业务对象</span><strong>{obligation.objectRefs.join('、') || '后端暂未提供'}</strong></div>
        <div><span>业务操作</span><strong>{obligation.operationRef || '后端暂未提供'}</strong></div>
        <div><span>适用角色</span><strong>{obligation.actorRefs.join('、') || '未限定 / 后端暂未提供'}</strong></div>
      </div>

      <div className="ti-contract-grid">
        <section>
          <span>前置条件</span>
          {preconditions.length ? <ul>{preconditions.map((item, index) => <li key={`${item}:${index}`}>{item}</li>)}</ul> : <p>未声明额外前置条件</p>}
        </section>
        <section>
          <span>预期结果</span>
          <ul>{outcomes.map((item, index) => <li key={`${item}:${index}`}>{item}</li>)}</ul>
        </section>
      </div>

      {obligation.businessConstraints.length > 0 && (
        <div className="ti-constraints">
          <span>业务约束</span>
          <div>{obligation.businessConstraints.map((item) => <code key={item}>{item}</code>)}</div>
        </div>
      )}

      {obligation.requirementFindingIds.length > 0 && (
        <div className="ti-requirement-links">
          <span>关联需求审查项</span>
          <div>{obligation.requirementFindingIds.map((id) => <code key={id}>{id}</code>)}</div>
          <p>仅展示后端已证明的精确关联；相似文本、同来源或邻近业务语义不会自动绑定。</p>
        </div>
      )}

      <div className="ti-status-strip" aria-label="测试义务状态">
        <span><b>设计</b>{obligation.designStatus}</span>
        <span><b>验证</b>{obligation.verificationStatus}</span>
        <span><b>运行时</b>{obligation.runtimeLinkage}</span>
        <span><b>风险</b>{obligation.riskLevel}</span>
      </div>

      <details className="ti-evidence-disclosure">
        <summary>查看来源证据 <span>{obligation.evidence.length} 条 · {obligation.sourceIds.length} 个来源</span></summary>
        <EvidenceList evidence={obligation.evidence} />
      </details>
    </article>
  );
}

function WorkspaceEmpty({ onMaterials }: { onMaterials: () => void }) {
  return (
    <section className="ti-empty">
      <span>Test Intelligence</span>
      <h1>先选择项目，再生成测试义务视图</h1>
      <p>接入 PRD、业务规则、接口、状态机等资料后，QualiBug 会从已有企业理解中投影需要验证的业务规则、状态流转、权限边界与业务副作用。</p>
      <button type="button" className="btn btn-primary" onClick={onMaterials}>查看资料接入</button>
    </section>
  );
}

export function TestIntelligence() {
  usePageTitle('测试智能');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const [analysis, setAnalysis] = useState<TestIntelligenceAnalysis | null>(null);
  const [loading, setLoading] = useState(Boolean(project));
  const [error, setError] = useState('');
  const [activeKind, setActiveKind] = useState<TestObligationKind | 'all'>('all');

  const load = useCallback(async () => {
    if (!project) {
      setAnalysis(null);
      setError('');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const next = await getTestIntelligence(project);
      setAnalysis(next);
      if (!next) setError('当前项目不可用，请重新选择项目。');
    } catch (caught: unknown) {
      setAnalysis(null);
      setError(caught instanceof Error ? caught.message : '测试智能数据读取失败');
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => { void load(); }, [load]);

  const obligations = useMemo(() => {
    if (!analysis) return [];
    if (activeKind === 'all') return analysis.obligations;
    return analysis.obligations.filter((item) => item.obligationKind === activeKind);
  }, [activeKind, analysis]);

  if (!project) return <WorkspaceEmpty onMaterials={() => navigateToProjectPath('/materials', '')} />;

  if (loading) {
    return (
      <section className="ti-loading" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <div><strong>正在读取 Test Intelligence…</strong><p>正在把已有业务语义投影为证据化 Test Obligation。</p></div>
      </section>
    );
  }

  if (error || !analysis) {
    return (
      <section className="ti-error" role="alert">
        <span>Test Intelligence 暂不可用</span>
        <h1>无法读取当前项目的测试义务</h1>
        <p>{error || '后端未返回有效分析结果。'}</p>
        <button type="button" className="btn btn-primary" onClick={() => void load()}>重新读取</button>
      </section>
    );
  }

  const { coverage, summary } = analysis;
  const statusMeta = COVERAGE_META[coverage.status];
  const counts = coverage.countsByObligationKind;

  return (
    <div className="ti-workspace">
      <header className={`ti-hero status-${coverage.status.toLowerCase()}`}>
        <div className="ti-hero-copy">
          <span className="ti-eyebrow">{statusMeta.eyebrow}</span>
          <h1>{statusMeta.title}</h1>
          <p>{statusMeta.description}</p>
          <div className="ti-hero-actions">
            <button type="button" className="btn btn-primary" onClick={() => navigateToProjectPath('/materials', project)}>管理企业资料</button>
            <button type="button" className="btn btn-secondary" onClick={() => void load()}>刷新测试义务</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigateToProjectPath('/requirements', project)}>查看需求审查</button>
          </div>
        </div>
        <div className="ti-coverage-card" aria-label="支持语义覆盖">
          <span>Supported Semantic Coverage</span>
          <strong>{coverage.status}</strong>
          <p>{coverage.eligibleSupportedSemanticUnitCount > 0
            ? `${coverage.obligatedSupportedSemanticUnitCount} / ${coverage.eligibleSupportedSemanticUnitCount} 个支持语义已形成义务`
            : '尚无可度量的支持语义'}</p>
          {coverage.eligibleSupportedSemanticUnitCount > 0 && (
            <div className="ti-progress" aria-label="已形成义务的支持语义比例">
              <span style={{ width: `${Math.min(100, (coverage.obligatedSupportedSemanticUnitCount / coverage.eligibleSupportedSemanticUnitCount) * 100)}%` }} />
            </div>
          )}
          <small>不是总测试完整率 · 执行覆盖 {coverage.executionCoverageStatus}</small>
        </div>
      </header>

      <section className="ti-metrics" aria-label="测试智能摘要">
        <article><span>资料来源</span><strong>{summary.sourceCount}</strong><p>参与当前企业理解的来源</p></article>
        <article><span>Test Obligation</span><strong>{summary.obligationCount}</strong><p>当前可交付的证据化测试义务</p></article>
        <article><span>支持语义单元</span><strong>{coverage.eligibleSupportedSemanticUnitCount}</strong><p>当前 v1 denominator，不是全部潜在测试空间</p></article>
        <article><span>未覆盖支持语义</span><strong>{coverage.uncoveredSupportedSemanticUnitCount}</strong><p>满足 denominator 但尚未形成义务</p></article>
      </section>

      <section className="ti-truth-note">
        <div><span>当前产品边界</span><strong>需求审查与测试义务只做可证明的精确关联</strong></div>
        <p>当前有 {summary.requirementFindingLinkedObligationCount} 个 Test Obligation 关联到 Requirement Finding。关联不会改变 OBLIGATION_ONLY / NOT_MEASURED / NOT_EVALUATED，也不会自动制造 requirement_risk 义务或宣称测试已经执行。</p>
      </section>

      {(summary.suppressedWithoutEvidenceCount > 0 || summary.unsupportedFormalBehaviorCount > 0) && (
        <section className="ti-diagnostics" aria-label="投影诊断">
          <div><span>因证据不足未提升</span><strong>{summary.suppressedWithoutEvidenceCount}</strong></div>
          <div><span>当前 v1 未支持正式行为</span><strong>{summary.unsupportedFormalBehaviorCount}</strong></div>
          <p>这些数字是投影边界诊断，不是缺陷数量，也不代表业务风险等级。</p>
        </section>
      )}

      <section className="ti-obligations-section">
        <div className="ti-section-heading">
          <div><span>Test Obligations</span><h2>必须验证的业务语义</h2></div>
          <div className="ti-filter" role="group" aria-label="按测试义务类型筛选">
            <button type="button" className={activeKind === 'all' ? 'active' : ''} onClick={() => setActiveKind('all')}>全部 {analysis.obligations.length}</button>
            {(Object.keys(KIND_META) as TestObligationKind[]).map((kind) => (
              <button key={kind} type="button" className={activeKind === kind ? 'active' : ''} onClick={() => setActiveKind(kind)} title={KIND_META[kind].description}>{KIND_META[kind].short} {counts[kind]}</button>
            ))}
          </div>
        </div>

        {obligations.length > 0 ? (
          <div className="ti-obligations-list">{obligations.map((item) => <ObligationCard obligation={item} key={item.obligationId} />)}</div>
        ) : (
          <div className="ti-no-obligations">
            <strong>{activeKind === 'all' ? '当前没有可交付 Test Obligation' : `当前没有${KIND_META[activeKind as TestObligationKind].label}义务`}</strong>
            <p>{coverage.status === 'NOT_MEASURED'
              ? '当前没有进入 v1 支持语义 denominator 的正式业务语义。系统不会把空集合显示成 100% 覆盖。'
              : '系统不会为了填满报告而生成无证据测试义务。'}</p>
          </div>
        )}
      </section>

      {coverage.uncoveredSupportedSemanticUnitIds.length > 0 && (
        <details className="ti-uncovered">
          <summary>查看未覆盖支持语义 <span>{coverage.uncoveredSupportedSemanticUnitIds.length} 个</span></summary>
          <div>{coverage.uncoveredSupportedSemanticUnitIds.map((id) => <code key={id}>{id}</code>)}</div>
        </details>
      )}
    </div>
  );
}
