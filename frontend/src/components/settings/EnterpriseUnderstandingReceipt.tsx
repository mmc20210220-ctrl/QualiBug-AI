import { useEffect, useState } from 'react';
import {
  AUTHORITY_DECISIONS_CHANGED_EVENT,
  listAuthorityDecisions,
  submitAuthorityDecision,
  type AuthorityDecisionRecord,
} from '../../api/authority-decisions';
import { asArray, asNum, asRecord, asText } from '../../lib/value-guards';

type JsonRecord = Record<string, unknown>;

type Props = {
  payload: unknown;
  loading: boolean;
  hasSources: boolean;
  project?: string;
  onAuthorityDecision?: () => void;
};

type ConflictEvidenceView = {
  sourceId: string;
  sourceName: string;
  sourceLocator: string;
  quote: string;
  factId: string;
  modality: string;
  documentVersion: string;
};

type ConflictView = {
  id: string;
  kind: string;
  message: string;
  operatorAction: string;
  automaticResolutionAllowed: boolean;
  authorityStatus: string;
  sourceScope: string;
  resolutionPolicy: string;
  disallowedSignals: string[];
  selectedFactId: string;
  evidence: ConflictEvidenceView[];
};

type UnderstandingView = {
  available: boolean;
  understandingReady: boolean;
  ready: boolean;
  status: string;
  statusLabel: string;
  modelId: string;
  sourceTraceabilityRate: number | null;
  businessObjectCount: number;
  actorCount: number;
  operationCount: number;
  lifecycleCount: number;
  processCount: number;
  scenarioCount: number;
  runtimePlanCount: number;
  runtimeMaterializationCount: number;
  unknownCount: number;
  conflictCount: number;
  blockers: string[];
  conflicts: ConflictView[];
  resolvedConflicts: ConflictView[];
  sourceLabels: Record<string, string>;
  gates: Array<{ label: string; status: string; ready: boolean }>;
};

function asBoolean(value: unknown): boolean {
  return value === true;
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    const text = asText(value);
    if (text) return text;
  }
  return '';
}

const reasonLabels: Record<string, string> = {
  SEMANTIC_UNDERSTANDING_NOT_CLOSED: '企业业务语义尚未闭合',
  IMPLEMENTATION_BINDING_NOT_CLOSED: '业务场景尚未唯一绑定到系统实现',
  IMPLEMENTATION_BINDING_CONFLICT: '业务场景与系统实现存在冲突',
  MODEL_SCHEMA_OR_EVIDENCE_INVALID: '部分理解结论缺少合格的结构或来源证据',
  UNRESOLVED_BUSINESS_FACT_OR_BEHAVIOR_CONFLICTS: '企业资料中的业务事实仍有未解决冲突',
  OPERATION_OBJECT_UNRESOLVED: '部分业务操作尚未确定唯一作用对象',
  EXECUTION_CONTRACT_SOURCE_EVIDENCE_MISSING: '部分执行场景缺少原始资料证据',
  EXECUTION_CONTRACT_AUTHORITATIVE_ACTION_ENTRY_MISSING: '部分业务场景尚未绑定权威执行入口',
  EXECUTION_CONTRACT_PERMISSION_RESPONSE_OBSERVER_UNRESOLVED: '权限结果缺少可验证的响应观察方式',
  EXECUTION_CONTRACT_EFFECT_OBSERVER_UNRESOLVED: '业务效果缺少可验证的观察方式',
  RUNTIME_PLAN_REQUEST_FIELD_LOCATION_UNRESOLVED: '请求字段在接口契约中的位置尚未明确',
  RUNTIME_PLAN_REQUEST_FIELD_LOCATION_AMBIGUOUS: '同一请求字段在接口契约中存在多个位置',
  RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS: '同一业务角色对应多个测试凭证引用',
  RUNTIME_PLAN_ORACLE_TEMPLATE_UNRESOLVED: '运行计划尚未形成可验证的观察模板',
  RUNTIME_PLAN_CLEANUP_TEMPLATE_UNRESOLVED: '写操作尚未形成安全清理模板',
  RUNTIME_MATERIALIZATION_ENVIRONMENT_REF_UNRESOLVED: '运行实例尚未唯一绑定测试环境',
  RUNTIME_MATERIALIZATION_BASE_URL_UNRESOLVED: '运行实例尚未获得测试环境地址',
  RUNTIME_MATERIALIZATION_ENVIRONMENT_REF_AMBIGUOUS: '运行实例匹配到多个候选测试环境',
  RUNTIME_MATERIALIZATION_PRODUCTION_WRITE_FORBIDDEN: '生产环境写入被安全策略禁止',
  RUNTIME_MATERIALIZATION_NON_PRODUCTION_ENVIRONMENT_UNPROVEN: '尚未证明当前环境为非生产测试环境',
  RUNTIME_MATERIALIZATION_CREDENTIAL_REF_UNRESOLVED: '运行实例尚未绑定对应角色的凭据引用',
  RUNTIME_MATERIALIZATION_REQUIRED_VALUE_BINDING_MISSING: '运行实例缺少必填动态值绑定',
  RUNTIME_MATERIALIZATION_REQUIRED_VALUE_BINDING_AMBIGUOUS: '必填动态值匹配到多个候选绑定',
  RUNTIME_MATERIALIZATION_VALUE_BINDING_NOT_APPROVED: '运行值绑定尚未批准用于测试',
  RUNTIME_MATERIALIZATION_FIXTURE_REF_UNRESOLVED: '运行实例引用的测试Fixture不可用或尚未批准',
  RUNTIME_MATERIALIZATION_MEDIA_TYPE_SELECTION_MISSING: '请求体存在多种媒体类型但尚未选择',
  RUNTIME_MATERIALIZATION_MEDIA_TYPE_SELECTION_AMBIGUOUS: '请求体媒体类型存在多个已批准候选',
  RUNTIME_MATERIALIZATION_ENTITY_IDENTITY_BINDING_UNRESOLVED: '前后快照尚未绑定同一业务实体标识',
  RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_MISSING: '运行实例缺少测试数据绑定',
  RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_AMBIGUOUS: '运行实例存在多个测试数据候选绑定',
  RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_NOT_APPROVED: '运行实例引用的测试数据尚未批准',
  RUNTIME_MATERIALIZATION_CLEANUP_BINDING_MISSING: '写操作缺少唯一补偿操作绑定',
  RUNTIME_MATERIALIZATION_CLEANUP_BINDING_AMBIGUOUS: '写操作匹配到多个补偿操作绑定',
  RUNTIME_MATERIALIZATION_SAFE_CLEANUP_CAPABILITY_UNRESOLVED: '写操作尚未绑定可验证的安全清理能力',
  RUNTIME_MATERIALIZATION_SENSITIVE_FIELD_REQUIRES_CREDENTIAL_REF: '敏感请求字段必须通过凭据引用注入',
  RUNTIME_MATERIALIZATION_SOURCE_EVIDENCE_MISSING: '运行实例缺少可追溯的企业资料证据',
  TECHNICAL_OPERATIONS_WITHOUT_BUSINESS_OPERATIONS: '接口契约尚未形成业务操作',
  CROSS_SOURCE_IDENTITY_UNRESOLVED: '中文业务对象与技术结构名尚未源声明绑定',
  TECHNICAL_RELATION_ENDPOINT_UNRESOLVED: '技术关系两端尚未对应已理解业务对象',
};

function readableReason(value: unknown): string {
  const code = asText(value);
  return reasonLabels[code] || code.replaceAll('_', ' ').trim();
}

function rowMessage(value: unknown): string {
  const row = asRecord(value);
  const details = asRecord(row.details);
  return firstText(
    row.message,
    row.description,
    row.question,
    row.reason,
    row.statement,
    row.raw_statement,
    row.resolution_policy,
    details.message,
    details.statement,
    details.reason,
    readableReason(row.reason_code),
    readableReason(row.kind),
  );
}

function sourceLabelMap(asset: JsonRecord): Record<string, string> {
  const labels: Record<string, string> = {};
  const candidates = [
    ...asArray(asset.sources),
    ...asArray(asset.source_inventory),
    ...asArray(asRecord(asset.source_registry).items),
  ];
  for (const candidate of candidates) {
    const row = asRecord(candidate);
    const id = firstText(row.source_id, row.id);
    const name = firstText(row.filename, row.name, row.source_name, row.title, row.path);
    if (id && name) labels[id] = name;
  }
  return labels;
}

function conflictEvidence(value: unknown, sourceLabels: Record<string, string>): ConflictEvidenceView[] {
  const row = asRecord(value);
  const candidates = [
    ...asArray(row.evidence),
    ...asArray(row.facts),
    ...asArray(row.source_evidence),
  ];
  const seen = new Set<string>();
  const result: ConflictEvidenceView[] = [];
  for (const candidate of candidates) {
    const item = asRecord(candidate);
    const sourceId = asText(item.source_id);
    const sourceLocator = asText(item.source_locator || item.locator);
    const quote = asText(item.quote || item.statement || item.raw_statement);
    const factId = asText(item.fact_id);
    const modality = asText(item.modality);
    const documentVersion = asText(item.document_version || item.version);
    const key = `${sourceId}|${sourceLocator}|${quote}|${factId}`;
    if (!sourceId && !sourceLocator && !quote && !factId) continue;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({
      sourceId,
      sourceName: sourceLabels[sourceId] || sourceId || '来源已记录',
      sourceLocator,
      quote,
      factId,
      modality,
      documentVersion,
    });
  }
  return result.slice(0, 4);
}

function conflictViews(values: unknown[], sourceLabels: Record<string, string>): ConflictView[] {
  const seen = new Set<string>();
  const result: ConflictView[] = [];
  for (const value of values) {
    const row = asRecord(value);
    const id = firstText(row.conflict_id, row.id, rowMessage(row));
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const authority = asRecord(row.authority_decision);
    const disallowed = asArray(row.disallowed_authority_signals).length
      ? asArray(row.disallowed_authority_signals)
      : asArray(authority.disallowed_authority_signals);
    result.push({
      id,
      kind: asText(row.kind) || asText(row.conflict_type) || asText(row.reason_code) || 'SOURCE_CONFLICT',
      message: rowMessage(row),
      operatorAction: firstText(
        row.operator_action,
        row.resolution_policy,
        authority.required_operator_action,
        '资料冲突保持未决；系统不会按时间、文件名、文档顺序或模型置信度自动选权威。',
      ),
      automaticResolutionAllowed: asBoolean(row.automatic_resolution_allowed),
      authorityStatus: (asText(authority.status) || asText(row.status) || 'UNRESOLVED').toUpperCase(),
      sourceScope: (asText(row.source_scope) || '').toUpperCase(),
      resolutionPolicy: firstText(row.resolution_policy, authority.resolution_policy),
      disallowedSignals: disallowed.map((item) => asText(item)).filter(Boolean),
      selectedFactId: asText(authority.selected_fact_id),
      evidence: conflictEvidence(row, sourceLabels),
    });
  }
  return result;
}

function gateView(label: string, value: unknown, readyKeys: string[]): { label: string; status: string; ready: boolean } {
  const gate = asRecord(value);
  const ready = readyKeys.some((key) => asBoolean(gate[key]));
  return {
    label,
    status: asText(gate.status) || 'NOT_BUILT',
    ready,
  };
}

function statusLabel(
  status: string,
  understandingReady: boolean,
  chainReady: boolean,
  available: boolean,
): string {
  if (!available) return '等待后台理解';
  if (chainReady) return '运行草稿链已闭合';
  if (understandingReady) return '理解已闭合，运行草稿链待完成';
  if (status.startsWith('BLOCKED')) return '理解被阻断';
  if (status.startsWith('PARTIAL')) return '理解部分完成';
  return '仍在理解中';
}

function projectUnderstanding(payload: unknown): UnderstandingView {
  const asset = asRecord(asRecord(payload).knowledge_asset);
  const summary = asRecord(asset.summary);
  const model = asRecord(asset.enterprise_understanding_model);
  const modelGate = asRecord(model.gate);
  const comprehensionGate = asRecord(asset.enterprise_comprehension_gate);
  const scenarioPlanningGate = asRecord(asset.scenario_planning_gate);
  const scenarioIrGate = asRecord(asset.scenario_ir_gate);
  const executionContractGate = asRecord(asset.scenario_execution_contract_gate);
  const runtimePlanGate = asRecord(asset.runtime_plan_gate);
  const materializationGate = asRecord(asset.runtime_materialization_gate);
  const metrics = asRecord(modelGate.metrics);
  const modelId = firstText(summary.enterprise_understanding_model_id, model.model_id);
  const status = firstText(
    summary.enterprise_understanding_status,
    modelGate.status,
    comprehensionGate.status,
    'NOT_BUILT',
  );
  const available = Boolean(modelId || Object.keys(model).length || status !== 'NOT_BUILT');
  const understandingReady = asBoolean(summary.enterprise_understanding_ready)
    || asBoolean(modelGate.entry_allowed)
    || asBoolean(comprehensionGate.entry_allowed);
  const sourceLabels = sourceLabelMap(asset);

  const criticalUnknowns = [
    ...asArray(modelGate.critical_unknowns),
    ...asArray(model.unknowns).filter((value) => asBoolean(asRecord(value).blocks_formal_understanding)),
  ];
  const unresolvedConflicts = [
    ...asArray(modelGate.unresolved_conflicts),
    ...asArray(model.conflicts).filter((value) => {
      const rowStatus = (asText(asRecord(value).status) || 'UNRESOLVED').toUpperCase();
      return !['RESOLVED', 'SUPERSEDED', 'DISMISSED'].includes(rowStatus);
    }),
    ...asArray(comprehensionGate.unresolved_business_fact_conflicts).filter((value) => {
      const rowStatus = (asText(asRecord(value).status) || 'UNRESOLVED').toUpperCase();
      return !['RESOLVED', 'SUPERSEDED', 'DISMISSED'].includes(rowStatus);
    }),
    ...asArray(asset.cross_document_conflicts).filter((value) => {
      const row = asRecord(value);
      const rowStatus = (asText(row.status) || 'UNRESOLVED').toUpperCase();
      if (['RESOLVED', 'SUPERSEDED', 'DISMISSED'].includes(rowStatus)) return false;
      // Authority-eligible technical or Chinese conflicts with selectable participants.
      return Boolean(asText(row.conflict_id) && conflictEvidence(row, sourceLabels).some((item) => item.factId));
    }),
  ];
  const resolvedConflictRows = [
    ...asArray(model.conflicts),
    ...asArray(asset.cross_document_conflicts),
  ].filter((value) => {
    const row = asRecord(value);
    const rowStatus = (asText(row.status) || '').toUpperCase();
    const authorityStatus = (asText(asRecord(row.authority_decision).status) || '').toUpperCase();
    return rowStatus === 'RESOLVED' || authorityStatus === 'RESOLVED';
  });
  const conflictDetails = conflictViews(unresolvedConflicts, sourceLabels).slice(0, 8);
  const resolvedConflicts = conflictViews(resolvedConflictRows, sourceLabels).slice(0, 8);
  const gateReasons = [
    ...asArray(modelGate.blocking_reasons),
    ...asArray(scenarioPlanningGate.blocking_reasons),
    ...asArray(scenarioIrGate.blocking_reasons),
    ...asArray(executionContractGate.blocking_reasons),
    ...asArray(runtimePlanGate.blocking_reasons),
    ...asArray(materializationGate.blocking_reasons),
  ].map(readableReason).filter(Boolean);
  const runtimeUnknowns = asArray(asset.runtime_plan_unknowns)
    .filter((value) => asBoolean(asRecord(value).blocks_runtime_plan));
  const materializationUnknowns = asArray(asset.runtime_materialization_unknowns)
    .filter((value) => asBoolean(asRecord(value).blocks_runtime_materialization));
  const gapMessages = asArray(asset.coverage_gaps)
    .map((value) => {
      const row = asRecord(value);
      const kind = asText(row.kind);
      if (!/(UNDERSTANDING|SCENARIO|EXECUTION_CONTRACT|RUNTIME_PLAN|RUNTIME_MATERIALIZATION)/.test(kind)) return '';
      return firstText(row.message, row.operator_action, readableReason(row.kind));
    })
    .filter(Boolean);
  const blockers = [...new Set([
    ...criticalUnknowns.map(rowMessage),
    ...unresolvedConflicts.map(rowMessage),
    ...runtimeUnknowns.map(rowMessage),
    ...materializationUnknowns.map(rowMessage),
    ...gateReasons,
    ...gapMessages,
  ].filter(Boolean))].slice(0, 8);

  const projection = Number(metrics.source_traceability_rate);
  const sourceTraceabilityRate = Number.isFinite(projection)
    ? Math.round(projection * 1000) / 10
    : null;
  const gates = [
    gateView('企业理解', modelGate, ['entry_allowed']),
    gateView('场景规划', scenarioPlanningGate, ['scenario_planning_allowed', 'entry_allowed']),
    gateView('Scenario IR', scenarioIrGate, ['entry_allowed']),
    gateView('执行合同', executionContractGate, ['execution_contract_ready', 'entry_allowed']),
    gateView('Runtime Plan', runtimePlanGate, ['runtime_plan_ready', 'entry_allowed']),
    gateView('运行实例化', materializationGate, ['runtime_materialization_ready', 'entry_allowed']),
  ];
  const ready = understandingReady && gates.every((gate) => gate.ready);

  return {
    available,
    understandingReady,
    ready,
    status,
    statusLabel: statusLabel(status, understandingReady, ready, available),
    modelId,
    sourceTraceabilityRate,
    businessObjectCount: asNum(summary.understood_business_object_count) || asArray(model.business_objects).length,
    actorCount: asNum(summary.understood_actor_count) || asArray(model.actors).length,
    operationCount: asNum(summary.understood_operation_count) || asArray(model.operations).length,
    lifecycleCount: asNum(summary.understood_lifecycle_count) || asArray(model.lifecycles).length,
    processCount: asNum(summary.understood_process_count) || asArray(model.processes).length,
    scenarioCount: asNum(summary.scenario_ir_count) || asArray(asset.scenario_ir).length,
    runtimePlanCount: asNum(summary.runtime_plan_count) || asArray(asset.runtime_plans).length,
    runtimeMaterializationCount: asNum(summary.runtime_materialization_count) || asArray(asset.runtime_materializations).length,
    unknownCount: asNum(summary.enterprise_understanding_unknown_count) || asArray(model.unknowns).length,
    // Prefer the authority-aware unresolved list that this receipt actually
    // renders. Summary.enterprise_understanding_conflict_count can lag or count
    // historical RESOLVED rows; never let that inflate "未解决资料冲突".
    conflictCount: unresolvedConflicts.length,
    blockers,
    conflicts: conflictDetails,
    resolvedConflicts,
    sourceLabels,
    gates,
  };
}

function sideLabel(index: number, total: number): string {
  if (total === 2) return index === 0 ? '方 A' : '方 B';
  return `对立方 ${index + 1}`;
}

function scopeLabel(scope: string): string {
  if (scope === 'CROSS_SOURCE') return '跨文档';
  if (scope === 'INTRA_SOURCE') return '同文档';
  return scope || '资料冲突';
}

export function EnterpriseUnderstandingReceipt({
  payload,
  loading,
  hasSources,
  project = '',
  onAuthorityDecision,
}: Props) {
  const view = projectUnderstanding(payload);
  const tone = loading ? 'neutral' : view.ready ? 'success' : view.available ? 'warning' : 'neutral';
  const [busyConflictId, setBusyConflictId] = useState('');
  const [decisionNote, setDecisionNote] = useState('');
  const [decisionError, setDecisionError] = useState('');
  const [rationaleDraft, setRationaleDraft] = useState<Record<string, string>>({});
  const [decisionHistory, setDecisionHistory] = useState<AuthorityDecisionRecord[]>([]);
  const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);

  async function refreshDecisionHistory() {
    if (!project) return;
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const ledger = await listAuthorityDecisions(project);
      setDecisionHistory(ledger.decisions);
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : String(error));
    } finally {
      setHistoryLoading(false);
    }
  }

  useEffect(() => {
    void refreshDecisionHistory();
    if (!project || typeof window === 'undefined') return undefined;
    const onChange = (event: Event) => {
      const detail = asRecord((event as CustomEvent).detail);
      if (asText(detail.project) && asText(detail.project) !== project) return;
      void refreshDecisionHistory();
    };
    window.addEventListener(AUTHORITY_DECISIONS_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(AUTHORITY_DECISIONS_CHANGED_EVENT, onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);

  async function handleAuthorityDecision(
    conflict: ConflictView,
    action: 'SELECT_FACT' | 'LEAVE_UNRESOLVED',
    selectedFactId = '',
  ) {
    if (!project) {
      setDecisionError('缺少项目上下文，无法记录权威裁决。');
      return;
    }
    if (action === 'SELECT_FACT') {
      const side = conflict.evidence.find((item) => item.factId === selectedFactId);
      const confirmed = window.confirm(
        [
          `确认选用事实 ${selectedFactId || '（未知）'} 为权威？`,
          side?.quote ? `原文：${side.quote}` : '',
          '对立事实将标记为 SUPERSEDED，理解门禁仅在权威一致后放行。',
          '系统不会按时间、文件名、顺序或模型置信度自动选权威。',
        ].filter(Boolean).join('\n'),
      );
      if (!confirmed) return;
    }
    setBusyConflictId(conflict.id);
    setDecisionError('');
    setDecisionNote('');
    try {
      const result = await submitAuthorityDecision({
        project,
        conflictId: conflict.id,
        action,
        selectedFactId,
        rationale: asText(rationaleDraft[conflict.id]),
      });
      const gateHint = result.comprehension_entry_allowed == null
        ? ''
        : result.comprehension_entry_allowed
          ? '理解门禁：已放行。'
          : '理解门禁：仍阻断。';
      const receiptHint = result.audit_receipt_id
        ? `审计回执：${result.audit_receipt_id}`
        : '';
      setDecisionNote(
        [
          action === 'SELECT_FACT'
            ? `已记录操作员权威裁决：选用事实 ${result.decision.selected_fact_id || selectedFactId}`
            : '已记录操作员显式保留未决；enterprise_comprehension_gate 继续阻断，需补充资料或后续再裁决。',
          gateHint,
          result.understanding_gate_status
            ? `理解模型门禁：${result.understanding_gate_status}`
            : '',
          receiptHint,
        ].filter(Boolean).join(' '),
      );
      setRationaleDraft((prev) => ({ ...prev, [conflict.id]: '' }));
      await refreshDecisionHistory();
      onAuthorityDecision?.();
    } catch (error) {
      setDecisionError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyConflictId('');
    }
  }

  function renderEvidenceSide(conflict: ConflictView, evidence: ConflictEvidenceView, index: number) {
    return (
      <div
        key={`${conflict.id}:${evidence.factId || evidence.sourceId}:${index}`}
        className="settings-card-note settings-mt-10"
      >
        <strong>
          {sideLabel(index, conflict.evidence.length)}
          {' · '}
          {evidence.sourceName}
        </strong>
        {evidence.modality && <p>模态：{evidence.modality}</p>}
        {evidence.sourceLocator && <p>位置：{evidence.sourceLocator}</p>}
        {evidence.documentVersion && <p>文档版本：{evidence.documentVersion}</p>}
        {evidence.quote && <p>原文：{evidence.quote}</p>}
        {evidence.factId && <small className="muted">业务事实：{evidence.factId}</small>}
        {project && evidence.factId && conflict.authorityStatus !== 'RESOLVED' && (
          <div className="settings-compact-row settings-mt-10">
            <button
              type="button"
              className="btn btn-secondary"
              disabled={busyConflictId === conflict.id}
              onClick={() => {
                void handleAuthorityDecision(conflict, 'SELECT_FACT', evidence.factId);
              }}
            >
              {busyConflictId === conflict.id ? '记录中…' : '选用此方为权威'}
            </button>
          </div>
        )}
      </div>
    );
  }

  return (
    <section className={`section-card status-card status-${tone} settings-mt-10`}>
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">已有知识资产的只读投影</span>
          <h3>QualiBug 对企业业务的理解</h3>
          <p className="settings-card-sub">
            这里直接展示后台现有企业理解模型和门禁结果，不创建第二套模型，也不要求用户确认或编辑 AI 生成的业务结构。
          </p>
        </div>
        <strong className={view.ready ? 'is-positive' : 'is-neutral'}>
          {loading ? '读取中' : view.statusLabel}
        </strong>
      </div>

      {!loading && !view.available && (
        <div className="settings-card-note">
          {hasSources
            ? '资料已经入库，后台尚未形成可读取的企业理解模型。请等待当前批次分析完成，或继续补充能说明业务规则、接口和状态流转的原始资料。'
            : '上传原始企业资料后，后台会自动建立业务对象、角色、操作、生命周期、流程和可验证场景。'}
        </div>
      )}

      {view.available && (
        <>
          <div className="settings-mini-stats">
            <div className="settings-mini-stat"><span>业务对象</span><strong>{view.businessObjectCount}</strong></div>
            <div className="settings-mini-stat"><span>角色</span><strong>{view.actorCount}</strong></div>
            <div className="settings-mini-stat"><span>业务操作</span><strong>{view.operationCount}</strong></div>
            <div className="settings-mini-stat"><span>生命周期</span><strong>{view.lifecycleCount}</strong></div>
            <div className="settings-mini-stat"><span>流程</span><strong>{view.processCount}</strong></div>
            <div className="settings-mini-stat"><span>正式场景</span><strong>{view.scenarioCount}</strong></div>
            <div className="settings-mini-stat"><span>运行模板</span><strong>{view.runtimePlanCount}</strong></div>
            <div className="settings-mini-stat"><span>实例草稿</span><strong>{view.runtimeMaterializationCount}</strong></div>
          </div>

          <div className="settings-info-list settings-mt-10">
            <div className="settings-info-row">
              <span>来源可追溯度</span>
              <strong>{view.sourceTraceabilityRate == null ? '尚未上报' : `${view.sourceTraceabilityRate}%`}</strong>
            </div>
            <div className="settings-info-row">
              <span>待关闭未知项</span>
              <strong>{view.unknownCount}</strong>
            </div>
            <div className="settings-info-row">
              <span>未解决资料冲突</span>
              <strong>{view.conflictCount}</strong>
            </div>
          </div>

          {!view.understandingReady && (
            <div className="settings-card-note settings-mt-10">
              <strong>企业理解尚未闭合，正式场景规划不会放行。</strong>
              <p>系统不会通过人工点击“确认正确”关闭缺口。请继续上传能够说明相关规则、角色、状态或接口契约的原始资料。</p>
              {view.blockers.length > 0 && (
                <ul>
                  {view.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
                </ul>
              )}
            </div>
          )}

          {(decisionNote || decisionError) && (
            <p
              className={`settings-inline-feedback settings-mt-10 ${decisionError ? 'is-negative' : ''}`}
              role="status"
            >
              {decisionError || decisionNote}
            </p>
          )}

          {view.conflicts.length > 0 && (
            <details className="settings-auth-section settings-mt-10" open>
              <summary>
                <strong>未解决资料冲突</strong>
                <span className="muted">{view.conflicts.length} 条，系统不会自动选权威</span>
              </summary>
              <div className="customer-secondary-grid settings-mt-10">
                {view.conflicts.map((conflict) => (
                  <article key={conflict.id} className="customer-secondary-card">
                    <span className="customer-value-kicker">
                      {scopeLabel(conflict.sourceScope)} · {conflict.kind}
                      {conflict.automaticResolutionAllowed ? '' : ' · 禁止自动裁决'}
                      {conflict.authorityStatus ? ` · ${conflict.authorityStatus}` : ''}
                    </span>
                    <h3>{conflict.message || conflict.kind}</h3>
                    {conflict.operatorAction && <p>{conflict.operatorAction}</p>}
                    {conflict.disallowedSignals.length > 0 && (
                      <p className="muted">
                        禁止自动权威信号：{conflict.disallowedSignals.join('、')}
                      </p>
                    )}
                    {conflict.evidence.length === 2 ? (
                      <div className="customer-secondary-grid settings-mt-10">
                        {conflict.evidence.map((evidence, index) => renderEvidenceSide(conflict, evidence, index))}
                      </div>
                    ) : conflict.evidence.length > 0 ? (
                      conflict.evidence.map((evidence, index) => renderEvidenceSide(conflict, evidence, index))
                    ) : (
                      <div className="settings-card-note settings-mt-10">
                        冲突回执尚未附对立原文。QualiBug 不会猜测权威版本，请补充可判定权威/版本的原始资料。
                      </div>
                    )}
                    {project && (
                      <div className="settings-mt-10">
                        <label className="form-group" htmlFor={`authority-rationale-${conflict.id}`}>
                          <span>裁决说明（可选）</span>
                          <textarea
                            id={`authority-rationale-${conflict.id}`}
                            rows={2}
                            value={rationaleDraft[conflict.id] || ''}
                            onChange={(event) => {
                              const next = event.target.value;
                              setRationaleDraft((prev) => ({ ...prev, [conflict.id]: next }));
                            }}
                            placeholder="记录为何选用此方，或为何显式保留未决。不会成为自动权威依据。"
                          />
                        </label>
                        <div className="settings-compact-row settings-mt-10">
                          <button
                            type="button"
                            className="btn btn-ghost"
                            disabled={busyConflictId === conflict.id}
                            onClick={() => {
                              void handleAuthorityDecision(conflict, 'LEAVE_UNRESOLVED');
                            }}
                          >
                            显式保留未决
                          </button>
                        </div>
                      </div>
                    )}
                  </article>
                ))}
              </div>
            </details>
          )}

          {view.resolvedConflicts.length > 0 && (
            <details className="settings-auth-section settings-mt-10">
              <summary>
                <strong>已裁决冲突</strong>
                <span className="muted">{view.resolvedConflicts.length} 条</span>
              </summary>
              <div className="customer-secondary-grid settings-mt-10">
                {view.resolvedConflicts.map((conflict) => (
                  <article key={`resolved:${conflict.id}`} className="customer-secondary-card muted">
                    <span className="customer-value-kicker">
                      已裁决 · {conflict.kind}
                      {conflict.selectedFactId ? ` · 权威事实 ${conflict.selectedFactId}` : ''}
                    </span>
                    <h3>{conflict.message || conflict.kind}</h3>
                    {conflict.evidence.length === 2 ? (
                      <div className="customer-secondary-grid settings-mt-10">
                        {conflict.evidence.map((evidence, index) => (
                          <div
                            key={`resolved-ev:${conflict.id}:${evidence.factId || index}`}
                            className="settings-card-note settings-mt-10"
                          >
                            <strong>
                              {sideLabel(index, conflict.evidence.length)}
                              {' · '}
                              {evidence.sourceName}
                              {conflict.selectedFactId && evidence.factId === conflict.selectedFactId
                                ? ' · 权威'
                                : evidence.factId
                                  ? ' · 已让位'
                                  : ''}
                            </strong>
                            {evidence.modality && <p>模态：{evidence.modality}</p>}
                            {evidence.quote && <p>原文：{evidence.quote}</p>}
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </article>
                ))}
              </div>
            </details>
          )}

          {project && (
            <details className="settings-auth-section settings-mt-10" open={decisionHistory.length > 0}>
              <summary>
                <strong>权威裁决记录</strong>
                <span className="muted">
                  {historyLoading ? '读取中' : `${decisionHistory.length} 条`}
                </span>
              </summary>
              {historyError && (
                <p className="settings-inline-feedback is-negative settings-mt-10" role="status">
                  {historyError}
                </p>
              )}
              {!historyLoading && decisionHistory.length === 0 && !historyError && (
                <p className="settings-card-note settings-mt-10">
                  尚无操作员权威裁决。冲突出现后可选用一方权威，或显式保留未决；系统不会自动挑选。
                </p>
              )}
              {decisionHistory.length > 0 && (
                <div className="settings-info-list settings-mt-10">
                  {decisionHistory.map((decision) => (
                    <div key={decision.decision_id || `${decision.conflict_id}:${decision.decided_at_utc}`} className="settings-info-row">
                      <span>
                        {decision.decided_at_utc || '时间未记录'}
                        {decision.actor_name ? ` · ${decision.actor_name}` : ''}
                        {decision.actor_role ? `/${decision.actor_role}` : ''}
                      </span>
                      <strong>
                        {decision.action}
                        {decision.action === 'SELECT_FACT' && decision.selected_fact_id
                          ? ` → ${decision.selected_fact_id}`
                          : ''}
                        {decision.action === 'LEAVE_UNRESOLVED' ? ' · 门禁继续阻断' : ''}
                        {decision.rationale ? ` · ${decision.rationale}` : ''}
                        {decision.audit_receipt_id ? (
                          <>
                            {' · '}
                            <code>{decision.audit_receipt_id}</code>
                          </>
                        ) : null}
                      </strong>
                    </div>
                  ))}
                </div>
              )}
            </details>
          )}

          {view.understandingReady && !view.ready && (
            <div className="settings-card-note settings-mt-10">
              <strong>企业理解已经闭合，但运行草稿链尚未全部放行。</strong>
              <p>请查看下方现有门禁回执。通常还需要实现绑定、执行合同、Runtime Plan，或测试环境、凭据引用、动态值、测试数据与安全清理绑定达到要求；系统不会把“理解完成”误报成“已经可以执行”。</p>
              {view.blockers.length > 0 && (
                <ul>
                  {view.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
                </ul>
              )}
            </div>
          )}

          {view.ready && (
            <div className="settings-card-note settings-mt-10">
              企业理解、场景规划、Scenario IR、执行合同、Runtime Plan和运行实例化门禁已经闭合。当前产物仍是不可发送请求草稿和不可执行断言草稿；秘密值未加载、生成器未运行、SQL未生成、网络调用和Bug判定仍被禁止。
            </div>
          )}

          <details className="settings-auth-section settings-mt-10">
            <summary><strong>查看现有门禁回执</strong> <span className="muted">只读诊断</span></summary>
            <div className="settings-info-list settings-mt-10">
              {view.gates.map((gate) => (
                <div key={gate.label} className="settings-info-row">
                  <span>{gate.label}</span>
                  <strong>{gate.ready ? 'PASS' : gate.status}</strong>
                </div>
              ))}
              {view.modelId && (
                <div className="settings-info-row">
                  <span>模型 ID</span>
                  <strong><code>{view.modelId}</code></strong>
                </div>
              )}
            </div>
          </details>
        </>
      )}
    </section>
  );
}
