import { asArray, asNum, asRecord, asText } from '../../lib/value-guards';

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

type SourceEvidenceView = {
  sourceId: string;
  sourceName: string;
  sourceLocator: string;
  quote: string;
  factId: string;
};

type BlockerReceiptView = {
  id: string;
  category: string;
  kind: string;
  message: string;
  operatorAction: string;
  blocking: boolean;
  sourceBacked: boolean;
  sourceEvidence: SourceEvidenceView[];
};

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
    {
      key: 'runtime_materialization',
      label: '运行实例化',
      status: asText(summary.runtime_materialization_status) || 'NOT_BUILT',
      ready: asBoolean(summary.runtime_materialization_ready),
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
  const byKey = new Map(projected.map((row) => [row.key, row]));
  return fallbackGates(summary).map((fallback) => byKey.get(fallback.key) || fallback);
}

function sourceEvidence(value: unknown): SourceEvidenceView[] {
  return asArray(value)
    .map(asRecord)
    .map((row) => ({
      sourceId: asText(row.source_id),
      sourceName: asText(row.source_name),
      sourceLocator: asText(row.source_locator),
      quote: asText(row.quote),
      factId: asText(row.fact_id),
    }))
    .filter((row) => row.sourceId || row.sourceName || row.sourceLocator || row.quote || row.factId);
}

function blockerReceipts(summary: JsonRecord): BlockerReceiptView[] {
  return asArray(summary.understanding_blocker_receipts)
    .map(asRecord)
    .map((row) => ({
      id: asText(row.receipt_id) || `${asText(row.category)}:${asText(row.kind)}:${asText(row.message)}`,
      category: asText(row.category),
      kind: asText(row.kind),
      message: asText(row.message),
      operatorAction: asText(row.operator_action),
      blocking: asBoolean(row.blocking),
      sourceBacked: asBoolean(row.source_backed),
      sourceEvidence: sourceEvidence(row.source_evidence),
    }))
    .filter((row) => row.id && row.message)
    .slice(0, 8);
}

const categoryLabels: Record<string, string> = {
  critical_unknown: '关键未知项',
  enterprise_unknown: '企业理解缺口',
  source_conflict: '资料冲突',
  scenario_ir_unknown: '场景缺口',
  execution_contract_unknown: '执行合同缺口',
  runtime_plan_unknown: '运行模板缺口',
  runtime_materialization_unknown: '运行实例化缺口',
  coverage_gap: '覆盖缺口',
};

function categoryLabel(receipt: BlockerReceiptView): string {
  return categoryLabels[receipt.category] || receipt.kind || '理解缺口';
}

export function EnterpriseUnderstandingPanel({ summary: value, onOpenMaterials }: Props) {
  const summary = asRecord(value);
  const modelId = asText(summary.enterprise_understanding_model_id);
  const understandingStatus = asText(summary.enterprise_understanding_status);
  const businessObjectCount = asNum(summary.understood_business_object_count);
  const operationCount = asNum(summary.understood_operation_count);
  const scenarioCount = asNum(summary.scenario_ir_count);
  const runtimePlanCount = asNum(summary.runtime_plan_count);
  const runtimeMaterializationCount = asNum(summary.runtime_materialization_count);
  const available = Boolean(
    modelId
    || (understandingStatus && understandingStatus !== 'NOT_BUILT')
    || businessObjectCount
    || operationCount
    || scenarioCount
    || runtimePlanCount
    || runtimeMaterializationCount,
  );
  if (!available) return null;

  const gates = understandingGates(summary);
  const understandingReady = asBoolean(summary.enterprise_understanding_ready);
  const chainReady = gates.every((gate) => gate.ready);
  const blockers = [...new Set(asArray(summary.understanding_blockers).map(asText).filter(Boolean))].slice(0, 8);
  const receipts = blockerReceipts(summary);
  const receiptMessages = new Set(receipts.map((receipt) => receipt.message));
  const residualBlockers = blockers.filter((blocker) => !receiptMessages.has(blocker));
  const firstBlocked = gates.find((gate) => !gate.ready);
  const statusTitle = chainReady
    ? '运行准备链已闭合'
    : understandingReady
      ? '企业理解已闭合，运行准备链待完善'
      : '企业理解尚未闭合';
  const statusDetail = chainReady
    ? '企业理解、场景规划、Scenario IR、执行合同、Runtime Plan 和运行实例化均已通过现有门禁。真实执行仍由现有 Experiment Executor 继续检查现场凭据、动态值、观察回执和清理恢复；当前草案本身仍不可直接发送或执行。'
    : firstBlocked
      ? `当前停在“${firstBlocked.label}”：${firstBlocked.status}。下方只读回执展示现有资产已经记录的资料来源或接入缺口；没有证据的条目不会被系统猜测补齐。`
      : '现有知识资产尚未形成完整的运行准备链。';

  return (
    <section className="focus-section" aria-label="企业理解状态">
      <div className="focus-section-head">
        <div>
          <span className="customer-value-kicker">已有知识资产的只读投影</span>
          <h2>QualiBug 对企业业务的理解</h2>
          <p>{statusTitle}。这里不创建第二套模型，也不要求用户人工确认或编辑业务结构。</p>
        </div>
        {!chainReady && <button className="btn btn-secondary" onClick={onOpenMaterials}>完善系统接入</button>}
      </div>

      <div className="customer-secondary-grid">
        <article className="customer-secondary-card">
          <span className="customer-value-kicker">理解与运行规模</span>
          <div className="customer-secondary-meta">
            <span><em>业务对象</em><b>{businessObjectCount}</b></span>
            <span><em>角色</em><b>{asNum(summary.understood_actor_count)}</b></span>
            <span><em>业务操作</em><b>{operationCount}</b></span>
            <span><em>正式场景</em><b>{scenarioCount}</b></span>
            <span><em>运行模板</em><b>{runtimePlanCount}</b></span>
            <span><em>实例化草案</em><b>{runtimeMaterializationCount}</b></span>
          </div>
        </article>

        <article className="customer-secondary-card">
          <span className="customer-value-kicker">理解质量回执</span>
          <div className="customer-secondary-meta">
            <span><em>来源可追溯度</em><b>{percent(summary.source_traceability_rate)}</b></span>
            <span><em>操作对象绑定</em><b>{percent(summary.operation_object_binding_rate)}</b></span>
            <span><em>生命周期完整度</em><b>{percent(summary.lifecycle_completeness)}</b></span>
            <span><em>待关闭未知项</em><b>{asNum(summary.enterprise_understanding_unknown_count)}</b></span>
            <span><em>运行实例化缺口</em><b>{asNum(summary.runtime_materialization_unknown_count)}</b></span>
            <span><em>有资料定位的缺口</em><b>{asNum(summary.understanding_source_receipt_count)}</b></span>
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
        {!chainReady && residualBlockers.length > 0 && receipts.length === 0 && (
          <ul>
            {residualBlockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
          </ul>
        )}
        <small className="muted">
          状态来源：现有 enterprise business knowledge asset。系统不会通过人工点击“确认正确”、常识补全或旧 Probe 回退绕过门禁。
        </small>
      </div>

      {!chainReady && receipts.length > 0 && (
        <details className="settings-auth-section settings-mt-10" open>
          <summary>
            <strong>查看缺口与原始资料回执</strong>
            <span className="muted">{receipts.length} 条现有回执</span>
          </summary>
          <div className="customer-secondary-grid settings-mt-10">
            {receipts.map((receipt) => (
              <article key={receipt.id} className="customer-secondary-card">
                <span className="customer-value-kicker">
                  {categoryLabel(receipt)} · {receipt.blocking ? '阻断' : '待补充'}
                </span>
                <h3>{receipt.message}</h3>
                {receipt.operatorAction && <p>{receipt.operatorAction}</p>}
                {receipt.sourceEvidence.length > 0 ? (
                  receipt.sourceEvidence.map((evidence, index) => (
                    <div
                      key={`${receipt.id}:${evidence.sourceId || evidence.sourceName}:${evidence.sourceLocator}:${index}`}
                      className="settings-card-note settings-mt-10"
                    >
                      <strong>{evidence.sourceName || evidence.sourceId || '来源已记录'}</strong>
                      {evidence.sourceLocator && <p>位置：{evidence.sourceLocator}</p>}
                      {evidence.quote && <p>原文：{evidence.quote}</p>}
                      {evidence.factId && <small className="muted">业务事实：{evidence.factId}</small>}
                    </div>
                  ))
                ) : (
                  <div className="settings-card-note settings-mt-10">
                    现有门禁回执尚未附具体资料定位。QualiBug 不会猜测来源，请补充对应企业资料，或完善测试环境、凭据、测试数据与安全清理能力。
                  </div>
                )}
              </article>
            ))}
          </div>
          {residualBlockers.length > 0 && (
            <div className="settings-card-note settings-mt-10">
              <strong>其余门禁原因</strong>
              <ul>{residualBlockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul>
            </div>
          )}
        </details>
      )}
    </section>
  );
}
