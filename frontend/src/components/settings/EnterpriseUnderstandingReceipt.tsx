type JsonRecord = Record<string, unknown>;

type Props = {
  payload: unknown;
  loading: boolean;
  hasSources: boolean;
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
  unknownCount: number;
  conflictCount: number;
  blockers: string[];
  gates: Array<{ label: string; status: string; ready: boolean }>;
};

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function asBoolean(value: unknown): boolean {
  return value === true;
}

function asNumber(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
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
    row.statement,
    row.raw_statement,
    details.message,
    details.statement,
    readableReason(row.reason_code),
    readableReason(row.kind),
  );
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
  if (chainReady) return '正式场景链已闭合';
  if (understandingReady) return '理解已闭合，场景链待完成';
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

  const criticalUnknowns = [
    ...asArray(modelGate.critical_unknowns),
    ...asArray(model.unknowns).filter((value) => asBoolean(asRecord(value).blocks_formal_understanding)),
  ];
  const conflicts = [
    ...asArray(modelGate.unresolved_conflicts),
    ...asArray(model.conflicts).filter((value) => {
      const rowStatus = (asText(asRecord(value).status) || 'UNRESOLVED').toUpperCase();
      return !['RESOLVED', 'SUPERSEDED', 'DISMISSED'].includes(rowStatus);
    }),
  ];
  const gateReasons = [
    ...asArray(modelGate.blocking_reasons),
    ...asArray(scenarioPlanningGate.blocking_reasons),
    ...asArray(scenarioIrGate.blocking_reasons),
    ...asArray(executionContractGate.blocking_reasons),
  ].map(readableReason).filter(Boolean);
  const gapMessages = asArray(asset.coverage_gaps)
    .map((value) => {
      const row = asRecord(value);
      const kind = asText(row.kind);
      if (!/(UNDERSTANDING|SCENARIO|EXECUTION_CONTRACT)/.test(kind)) return '';
      return firstText(row.message, row.operator_action, readableReason(row.kind));
    })
    .filter(Boolean);
  const blockers = [...new Set([
    ...criticalUnknowns.map(rowMessage),
    ...conflicts.map(rowMessage),
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
    businessObjectCount: asNumber(summary.understood_business_object_count) || asArray(model.business_objects).length,
    actorCount: asNumber(summary.understood_actor_count) || asArray(model.actors).length,
    operationCount: asNumber(summary.understood_operation_count) || asArray(model.operations).length,
    lifecycleCount: asNumber(summary.understood_lifecycle_count) || asArray(model.lifecycles).length,
    processCount: asNumber(summary.understood_process_count) || asArray(model.processes).length,
    scenarioCount: asNumber(summary.scenario_ir_count) || asArray(asset.scenario_ir).length,
    unknownCount: asNumber(summary.enterprise_understanding_unknown_count) || asArray(model.unknowns).length,
    conflictCount: asNumber(summary.enterprise_understanding_conflict_count) || conflicts.length,
    blockers,
    gates,
  };
}

export function EnterpriseUnderstandingReceipt({ payload, loading, hasSources }: Props) {
  const view = projectUnderstanding(payload);
  const tone = loading ? 'neutral' : view.ready ? 'success' : view.available ? 'warning' : 'neutral';

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

          {view.understandingReady && !view.ready && (
            <div className="settings-card-note settings-mt-10">
              <strong>企业理解已经闭合，但正式场景链尚未全部放行。</strong>
              <p>请查看下方现有门禁回执。通常还需要已有资料中的实现绑定、可观察结果或执行合同达到要求；系统不会把“理解完成”误报成“已经可以执行”。</p>
              {view.blockers.length > 0 && (
                <ul>
                  {view.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
                </ul>
              )}
            </div>
          )}

          {view.ready && (
            <div className="settings-card-note settings-mt-10">
              企业理解、场景规划、Scenario IR 和执行合同门禁已经闭合。运行时仍会继续核对环境、凭据、测试数据、观察通道和清理义务；这里不宣称理解准确率或业务召回率达到某个百分比。
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
