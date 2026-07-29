type JsonRecord = Record<string, unknown>;

type Props = {
  summary: unknown;
  onOpenMaterials: () => void;
};

type GateView = {
  key: string;
  label: string;
  status: string;
  ready: boolean;
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

function asNumber(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function asBoolean(value: unknown): boolean {
  return value === true;
}

function percent(value: unknown): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return '尚未上报';
  const normalized = parsed <= 1 ? parsed * 100 : parsed;
  return `${Math.round(normalized * 10) / 10}%`;
}

function fallbackGates(summary: JsonRecord): GateView[] {
  return [
    {
      key: 'enterprise_understanding',
      label: '企业理解',
      status: asText(summary.enterprise_understanding_status) || 'NOT_BUILT',
      ready: asBoolean(summary.enterprise_understanding_ready),
    },
    {
      key: 'scenario_planning',
      label: '场景规划',
      status: asText(summary.scenario_planning_status) || 'NOT_BUILT',
      ready: asBoolean(summary.scenario_planning_ready),
    },
    {
      key: 'scenario_ir',
      label: 'Scenario IR',
      status: asText(summary.scenario_ir_status) || 'NOT_BUILT',
      ready: asBoolean(summary.scenario_ir_ready),
    },
    {
      key: 'scenario_execution_contract',
      label: '执行合同',
      status: asText(summary.scenario_execution_contract_status) || 'NOT_BUILT',
      ready: asBoolean(summary.scenario_execution_contract_ready),
    },
    {
      key: 'runtime_plan',
      label: 'Runtime Plan',
      status: asText(summary.runtime_plan_status) || 'NOT_BUILT',
      ready: asBoolean(summary.runtime_plan_ready),
    },
  ];
}

function understandingGates(summary: JsonRecord): GateView[] {
  const projected = asArray(summary.understanding_gates)
    .map(asRecord)
    .map((row) => ({
      key: asText(row.key) || asText(row.label),
      label: asText(row.label),
      status: asText(row.status) || 'NOT_BUILT',
      ready: asBoolean(row.ready),
    }))
    .filter((row) => row.key && row.label);
  return projected.length > 0 ? projected : fallbackGates(summary);
}

export function EnterpriseUnderstandingPanel({ summary: value, onOpenMaterials }: Props) {
  const summary = asRecord(value);
  const modelId = asText(summary.enterprise_understanding_model_id);
  const understandingStatus = asText(summary.enterprise_understanding_status);
  const businessObjectCount = asNumber(summary.understood_business_object_count);
  const operationCount = asNumber(summary.understood_operation_count);
  const scenarioCount = asNumber(summary.scenario_ir_count);
  const runtimePlanCount = asNumber(summary.runtime_plan_count);
  const available = Boolean(
    modelId
    || (understandingStatus && understandingStatus !== 'NOT_BUILT')
    || businessObjectCount
    || operationCount
    || scenarioCount
    || runtimePlanCount,
  );
  if (!available) return null;

  const gates = understandingGates(summary);
  const understandingReady = asBoolean(summary.enterprise_understanding_ready);
  const chainReady = gates.length > 0
    ? gates.every((gate) => gate.ready)
    : asBoolean(summary.formal_scenario_chain_ready);
  const blockers = [...new Set(asArray(summary.understanding_blockers).map(asText).filter(Boolean))].slice(0, 6);
  const firstBlocked = gates.find((gate) => !gate.ready);
  const statusTitle = chainReady
    ? '运行模板链已闭合'
    : understandingReady
      ? '企业理解已闭合，运行模板链待完善'
      : '企业理解尚未闭合';
  const statusDetail = chainReady
    ? '企业理解、场景规划、Scenario IR、执行合同和 Runtime Plan 均已通过现有门禁。运行时仍会继续检查环境、凭据、测试数据、观察通道和清理义务。'
    : firstBlocked
      ? `当前停在“${firstBlocked.label}”：${firstBlocked.status}。请补充能够说明相关业务规则、状态流转、接口契约或运行约束的原始资料。`
      : '现有知识资产尚未形成完整的运行模板链。';

  return (
    <section className="focus-section" aria-label="企业理解状态">
      <div className="focus-section-head">
        <div>
          <span className="customer-value-kicker">已有知识资产的只读投影</span>
          <h2>QualiBug 对企业业务的理解</h2>
          <p>{statusTitle}。这里不创建第二套模型，也不要求用户人工确认或编辑业务结构。</p>
        </div>
        {!chainReady && <button className="btn btn-secondary" onClick={onOpenMaterials}>补充企业资料</button>}
      </div>

      <div className="customer-secondary-grid">
        <article className="customer-secondary-card">
          <span className="customer-value-kicker">理解规模</span>
          <div className="customer-secondary-meta">
            <span><em>业务对象</em><b>{businessObjectCount}</b></span>
            <span><em>角色</em><b>{asNumber(summary.understood_actor_count)}</b></span>
            <span><em>业务操作</em><b>{operationCount}</b></span>
            <span><em>生命周期</em><b>{asNumber(summary.understood_lifecycle_count)}</b></span>
            <span><em>正式场景</em><b>{scenarioCount}</b></span>
            <span><em>运行模板</em><b>{runtimePlanCount}</b></span>
          </div>
        </article>

        <article className="customer-secondary-card">
          <span className="customer-value-kicker">理解质量回执</span>
          <div className="customer-secondary-meta">
            <span><em>来源可追溯度</em><b>{percent(summary.source_traceability_rate)}</b></span>
            <span><em>操作对象绑定</em><b>{percent(summary.operation_object_binding_rate)}</b></span>
            <span><em>生命周期完整度</em><b>{percent(summary.lifecycle_completeness)}</b></span>
            <span><em>待关闭未知项</em><b>{asNumber(summary.enterprise_understanding_unknown_count)}</b></span>
            <span><em>未解决冲突</em><b>{asNumber(summary.enterprise_understanding_conflict_count)}</b></span>
            <span><em>运行模板缺口</em><b>{asNumber(summary.runtime_plan_unknown_count)}</b></span>
          </div>
        </article>
      </div>

      <div className="settings-info-list settings-mt-10">
        {gates.map((gate) => (
          <div key={gate.key} className="settings-info-row">
            <span>{gate.label}</span>
            <strong>{gate.ready ? 'PASS' : gate.status}</strong>
          </div>
        ))}
      </div>

      <div className="settings-card-note settings-mt-10">
        <strong>{statusTitle}</strong>
        <p>{statusDetail}</p>
        {!chainReady && blockers.length > 0 && (
          <ul>
            {blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
          </ul>
        )}
        <small className="muted">
          状态来源：现有 enterprise business knowledge asset。系统不会通过人工点击“确认正确”或常识补全绕过门禁。
        </small>
      </div>
    </section>
  );
}
