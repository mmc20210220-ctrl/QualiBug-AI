import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  getRequirementIntelligence,
  type RequirementEvidence,
  type RequirementFinding,
  type RequirementFindingType,
  type RequirementIntelligenceAnalysis,
  type RequirementReadinessStatus,
} from '../api/requirement-intelligence';
import { usePageTitle } from '../lib/page-title';
import { useProjectNavigation } from '../lib/project-navigation';
import './RequirementIntelligence.css';

const TYPE_META: Record<RequirementFindingType, { label: string; short: string }> = {
  requirement_conflict: { label: '跨资料冲突', short: '冲突' },
  requirement_missing: { label: '定义缺失', short: '缺失' },
  requirement_ambiguity: { label: '业务歧义', short: '歧义' },
};

const READINESS_META: Record<RequirementReadinessStatus, {
  eyebrow: string;
  title: string;
  description: string;
}> = {
  NOT_READY: {
    eyebrow: '需求就绪状态 · 未就绪',
    title: '当前需求存在阻塞项，建议先澄清再进入实现',
    description: '至少一项企业资料冲突或关键业务定义缺失正在阻塞正式理解。以下结论只来自可追溯资料证据。',
  },
  REVIEW_REQUIRED: {
    eyebrow: '需求就绪状态 · 待确认',
    title: '没有硬阻塞，但仍有需求需要人工确认',
    description: '当前发现的是非阻塞定义缺口或业务对象歧义。系统不会自行补全或自动合并业务事实。',
  },
  READY: {
    eyebrow: '需求就绪状态 · 可评审',
    title: '当前支持范围内没有待处理需求审查项',
    description: '这表示当前已投影的冲突、生命周期缺失与身份歧义均未形成活动 Finding，不代表完整性或召回率为 100%。',
  },
};

function evidenceAnchor(evidence: RequirementEvidence): string {
  return evidence.sourceLocator || evidence.assetRef || evidence.documentBlockId || evidence.documentNodeId || evidence.factId;
}

function EvidenceList({ evidence }: { evidence: RequirementEvidence[] }) {
  if (!evidence.length) return null;
  return (
    <div className="ri-evidence-list" aria-label="来源证据">
      {evidence.map((item, index) => (
        <article className="ri-evidence" key={`${item.sourceId}:${evidenceAnchor(item)}:${index}`}>
          <div className="ri-evidence-head">
            <strong>{item.sourceId || '企业资料'}</strong>
            {evidenceAnchor(item) && <code>{evidenceAnchor(item)}</code>}
          </div>
          {item.quote && <blockquote>{item.quote}</blockquote>}
          <div className="ri-evidence-meta">
            {item.factId && <span>Fact: {item.factId}</span>}
            {item.derivation && <span>{item.derivation}</span>}
          </div>
        </article>
      ))}
    </div>
  );
}

function FindingCard({ finding }: { finding: RequirementFinding }) {
  const meta = TYPE_META[finding.findingType];
  return (
    <article className={`ri-finding ${finding.blocking ? 'is-blocking' : 'needs-review'}`}>
      <div className="ri-finding-head">
        <div>
          <div className="ri-tags">
            <span className={`ri-tag type-${finding.findingType}`}>{meta.label}</span>
            {finding.blocking ? <span className="ri-tag blocker">阻塞</span> : <span className="ri-tag review">待确认</span>}
            {finding.severity && <span className="ri-tag neutral">{finding.severity}</span>}
          </div>
          <h3>{finding.title}</h3>
        </div>
        <span className="ri-finding-id" title={finding.findingId}>{finding.findingId}</span>
      </div>

      {finding.description && <p className="ri-finding-description">{finding.description}</p>}

      {(finding.relatedObjectRefs.length > 0 || finding.relatedOperationRefs.length > 0) && (
        <div className="ri-context-row">
          {finding.relatedObjectRefs.length > 0 && <span>业务对象：{finding.relatedObjectRefs.join('、')}</span>}
          {finding.relatedOperationRefs.length > 0 && <span>业务操作：{finding.relatedOperationRefs.join('、')}</span>}
        </div>
      )}

      {finding.operatorAction && (
        <div className="ri-action-callout">
          <span>建议确认</span>
          <p>{finding.operatorAction}</p>
        </div>
      )}

      <details className="ri-evidence-disclosure" open={finding.blocking}>
        <summary>
          查看证据
          <span>{finding.evidence.length} 条 · {finding.sourceIds.length} 个来源</span>
        </summary>
        <EvidenceList evidence={finding.evidence} />
      </details>
    </article>
  );
}

function WorkspaceEmpty({ onMaterials }: { onMaterials: () => void }) {
  return (
    <section className="ri-empty">
      <span>Requirement Intelligence</span>
      <h1>先选择项目，再开始需求审查</h1>
      <p>上传 PRD、接口文档、业务规则、状态机和历史资料后，QualiBug 会基于已有企业理解结果审查跨资料冲突、关键定义缺失与业务歧义。</p>
      <button type="button" className="btn btn-primary" onClick={onMaterials}>查看资料接入</button>
    </section>
  );
}

export function RequirementIntelligence() {
  usePageTitle('需求审查');
  const [params] = useSearchParams();
  const project = params.get('project')?.trim() || '';
  const { navigateToProjectPath } = useProjectNavigation();
  const [analysis, setAnalysis] = useState<RequirementIntelligenceAnalysis | null>(null);
  const [loading, setLoading] = useState(Boolean(project));
  const [error, setError] = useState('');
  const [activeType, setActiveType] = useState<RequirementFindingType | 'all'>('all');
  const requestGeneration = useRef(0);

  const load = useCallback(async () => {
    const generation = ++requestGeneration.current;
    if (!project) {
      setAnalysis(null);
      setError('');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const next = await getRequirementIntelligence(project);
      if (generation !== requestGeneration.current) return;
      setAnalysis(next);
      if (!next) setError('当前项目不可用，请重新选择项目。');
    } catch (caught: unknown) {
      if (generation !== requestGeneration.current) return;
      setError(caught instanceof Error ? caught.message : '需求审查数据读取失败');
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  }, [project]);

  useEffect(() => {
    void load();
    return () => {
      requestGeneration.current += 1;
    };
  }, [load]);

  const currentAnalysis = analysis?.projectId === project ? analysis : null;

  const findings = useMemo(() => {
    if (!currentAnalysis) return [];
    if (activeType === 'all') return currentAnalysis.findings;
    return currentAnalysis.findings.filter((finding) => finding.findingType === activeType);
  }, [activeType, currentAnalysis]);

  if (!project) {
    return <WorkspaceEmpty onMaterials={() => navigateToProjectPath('/materials', '')} />;
  }

  if (!currentAnalysis && loading) {
    return (
      <section className="ri-loading" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <div>
          <strong>正在读取需求审查结果…</strong>
          <p>正在汇总已有企业资料中的冲突、缺失、歧义与证据。</p>
        </div>
      </section>
    );
  }

  if (!currentAnalysis) {
    return (
      <section className="ri-error" role="alert">
        <span>需求审查暂不可用</span>
        <h1>无法读取当前项目的 Requirement Intelligence 结果</h1>
        <p>{error || '后端未返回有效分析结果。'}</p>
        <button type="button" className="btn btn-primary" onClick={() => void load()}>重新读取</button>
      </section>
    );
  }

  const readiness = currentAnalysis.readiness;
  const readinessMeta = READINESS_META[readiness.status];
  const counts = readiness.countsByType;

  return (
    <div className="ri-workspace">
      {loading && (
        <section className="ri-scope-note" role="status" aria-live="polite">
          <div><span>后台刷新</span><strong>保留当前审查结果，不阻塞页面操作</strong></div>
          <p>正在读取最新 Requirement Intelligence；完成后会原位更新。</p>
        </section>
      )}
      {!loading && error && (
        <section className="ri-scope-note" role="alert">
          <div><span>刷新失败</span><strong>当前仍显示上一次成功结果</strong></div>
          <p>{error}</p>
        </section>
      )}

      <header className={`ri-hero status-${readiness.status.toLowerCase()}`}>
        <div className="ri-hero-copy">
          <span className="ri-eyebrow">{readinessMeta.eyebrow}</span>
          <h1>{readinessMeta.title}</h1>
          <p>{readinessMeta.description}</p>
          <div className="ri-hero-actions">
            <button type="button" className="btn btn-primary" onClick={() => navigateToProjectPath('/materials', project)}>
              管理企业资料
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => void load()} disabled={loading}>
              {loading ? '刷新中…' : '刷新审查结果'}
            </button>
          </div>
        </div>
        <div className="ri-readiness-card" aria-label="需求就绪门禁">
          <span>Requirement Readiness</span>
          <strong>{readiness.status}</strong>
          <p>{readiness.blockingFindingCount > 0
            ? `${readiness.blockingFindingCount} 个阻塞项必须先处理`
            : readiness.reviewRequiredFindingCount > 0
              ? `${readiness.reviewRequiredFindingCount} 个事项需要人工确认`
              : '当前没有活动审查项'}</p>
        </div>
      </header>

      <section className="ri-metrics" aria-label="需求审查摘要">
        <article>
          <span>资料来源</span>
          <strong>{currentAnalysis.summary.sourceCount}</strong>
          <p>参与当前企业理解的来源</p>
        </article>
        <article>
          <span>跨资料冲突</span>
          <strong>{counts.requirement_conflict}</strong>
          <p>相同业务事实出现不兼容定义</p>
        </article>
        <article>
          <span>定义缺失</span>
          <strong>{counts.requirement_missing}</strong>
          <p>生命周期或业务语义尚未完整定义</p>
        </article>
        <article>
          <span>业务歧义</span>
          <strong>{counts.requirement_ambiguity}</strong>
          <p>需要人工确认的业务对象身份问题</p>
        </article>
      </section>

      <section className="ri-scope-note">
        <div>
          <span>当前审查边界</span>
          <strong>只展示可追溯、已有 authority 支撑的 Finding</strong>
        </div>
        <p>本页不是“AI 需求质量打分”。READY 仅表示当前支持的 Conflict / Missing / Ambiguity 没有活动 Finding，不等于资料完整率或问题召回率为 100%。</p>
      </section>

      <section className="ri-findings-section">
        <div className="ri-section-heading">
          <div>
            <span>Requirement Findings</span>
            <h2>待处理需求审查项</h2>
          </div>
          <div className="ri-filter" role="group" aria-label="按问题类型筛选">
            <button type="button" className={activeType === 'all' ? 'active' : ''} onClick={() => setActiveType('all')}>
              全部 {currentAnalysis.findings.length}
            </button>
            {(Object.keys(TYPE_META) as RequirementFindingType[]).map((type) => (
              <button key={type} type="button" className={activeType === type ? 'active' : ''} onClick={() => setActiveType(type)}>
                {TYPE_META[type].short} {counts[type]}
              </button>
            ))}
          </div>
        </div>

        {findings.length > 0 ? (
          <div className="ri-findings-list">
            {findings.map((finding) => <FindingCard finding={finding} key={finding.findingId} />)}
          </div>
        ) : (
          <div className="ri-no-findings">
            <strong>{activeType === 'all' ? '当前没有活动需求审查项' : `当前没有${TYPE_META[activeType as RequirementFindingType].label}`}</strong>
            <p>系统不会为了填满报告而生成无证据结论。新增或更新企业资料后可重新运行审查。</p>
          </div>
        )}
      </section>
    </div>
  );
}
