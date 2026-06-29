import Link from "next/link";
import { DecisionSummary } from "@/components/value-summary/DecisionSummary";
import { getCommandCenterSnapshot, getEnvironmentReadiness, getRiskDetail, getValueMetrics, listRisks, toSafeErrorView } from "@/lib/api/command-center";
import { redactUnknown } from "@/lib/redact";

function pickRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function pickString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function pickNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pickBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function pickArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function safeText(value: unknown, fallback = "—"): string {
  if (typeof value === "number") return String(value);
  const raw = pickString(value);
  if (!raw) return fallback;
  const redacted = redactUnknown(raw);
  return typeof redacted === "string" ? redacted : fallback;
}

function pickMetricNumber(source: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = pickNumber(source[key]);
    if (value !== null) return value;
  }
  return null;
}

function formatCurrencyRange(input: { min?: number | null; max?: number | null; currency?: string | null }): string {
  const min = input.min ?? null;
  const max = input.max ?? null;
  const ccy = input.currency ?? "CNY";
  if (min === null && max === null) return "—";
  if (min !== null && max !== null) return `${min.toLocaleString()} - ${max.toLocaleString()} ${ccy}`;
  return `${(min ?? max ?? 0).toLocaleString()} ${ccy}`;
}

function formatPercent(value: number | null): string {
  if (value === null) return "—";
  const normalized = value <= 1 ? value * 100 : value;
  const digits = normalized >= 10 || Number.isInteger(normalized) ? 0 : 1;
  return `${normalized.toFixed(digits)}%`;
}

function hasDeliverableEvidencePack(detail: Record<string, unknown>): boolean {
  const evidence = pickRecord(detail.evidence_bundle) ?? detail;
  return (
    pickArray(evidence.reproduction_steps).length > 0 ||
    pickArray(evidence.replay_timeline).length > 0 ||
    pickArray(evidence.closure_criteria).length > 0 ||
    pickArray(evidence.discovery_path).length > 0 ||
    pickRecord(evidence.request_summary) !== null ||
    pickRecord(evidence.response_summary) !== null
  );
}

function isCustomerReadyFinding(detail: Record<string, unknown>): boolean {
  const risk = pickRecord(detail.risk) ?? {};
  const evidence = pickRecord(detail.evidence_bundle) ?? detail;
  const explicit = pickBoolean(detail.customer_ready ?? risk.customer_ready ?? evidence.customer_ready);
  if (explicit !== null) return explicit;
  return pickArray(evidence.reproduction_steps).length > 0 && pickArray(evidence.closure_criteria).length > 0 && hasDeliverableEvidencePack(detail);
}

export default async function RoiValuePage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  const p = encodeURIComponent(projectId);

  try {
    const [snapshotEnvelope, valueEnvelope, environmentEnvelope, risksEnvelope] = await Promise.all([
      getCommandCenterSnapshot(projectId),
      getValueMetrics(projectId),
      getEnvironmentReadiness(projectId),
      listRisks(projectId),
    ]);
    const snapshot = pickRecord(snapshotEnvelope.data) ?? {};
    const valueMetrics = pickRecord(valueEnvelope.data) ?? {};
    const metrics = Object.keys(valueMetrics).length ? valueMetrics : (pickRecord(snapshot.value_metrics) ?? {});
    const environment = pickRecord(environmentEnvelope.data) ?? {};
    const riskSummary = pickRecord(snapshot.risk_summary) ?? {};
    const businessSummary = pickRecord(snapshot.business_flow_summary) ?? {};
    const risks = pickArray(risksEnvelope.data)
      .map((item) => pickRecord(item))
      .filter((item): item is Record<string, unknown> => Boolean(item));

    const riskIds = risks.map((risk) => pickString(risk.risk_id)).filter((value): value is string => Boolean(value));
    const savedHours = pickNumber(metrics.estimated_hours_saved);
    const trust = pickNumber(metrics.evidence_trust_score);
    const impactMin = pickNumber(metrics.estimated_business_impact_min);
    const impactMax = pickNumber(metrics.estimated_business_impact_max);
    const currency = pickString(metrics.currency);
    const blockerCount =
      pickMetricNumber(riskSummary, ["launch_blocking", "launch_blocking_risks", "blocking_risk_count"]) ??
      risks.filter((risk) => pickBoolean(risk.launch_blocking) === true).length;
    const coverageRate =
      pickMetricNumber(metrics, ["business_flow_coverage_rate", "coverage_rate"]) ??
      pickMetricNumber(businessSummary, ["coverage_rate"]);
    const coveredFlows = pickNumber(businessSummary.covered);
    const totalFlows = pickNumber(businessSummary.total);
    const requiredInputs = pickArray(environment.required_customer_inputs).map((item, index) => {
      const record = pickRecord(item) ?? {};
      return {
        key: `${safeText(record.title, `客户配合项 ${index + 1}`)}-${index}`,
        title: safeText(record.title, `客户配合项 ${index + 1}`),
        priority: safeText(record.priority, "medium").toUpperCase(),
        status: safeText(record.status, "pending"),
        whyNeeded: safeText(record.why_needed, "用于补齐环境接入与交付准入。"),
        suggestedInput: safeText(record.suggested_input, "请客户补齐对应资料。"),
        affectedFlows: pickArray(record.affected_flows).map((flow) => safeText(flow, "")).filter(Boolean),
      };
    });

    const directCustomerReadyCount = pickMetricNumber(metrics, [
      "customer_ready_finding_count",
      "approved_customer_ready_candidate_count",
      "customer_ready_reproduction_count",
    ]);
    const directEvidencePackCount = pickMetricNumber(metrics, [
      "deliverable_evidence_pack_count",
      "evidence_pack_count",
      "customer_ready_reproduction_count",
    ]);
    const directCustomerInputCount = pickMetricNumber(metrics, [
      "required_customer_input_count",
      "customer_cooperation_item_count",
    ]);

    let customerReadyCount = directCustomerReadyCount;
    let evidencePackCount = directEvidencePackCount;
    let derivedFromDetail = false;

    if ((customerReadyCount === null || evidencePackCount === null) && riskIds.length) {
      const detailResults = await Promise.allSettled(riskIds.map((riskId) => getRiskDetail(projectId, riskId)));
      const detailRecords: Record<string, unknown>[] = [];
      for (const result of detailResults) {
        if (result.status !== "fulfilled") continue;
        const record = pickRecord(result.value.data);
        if (record) detailRecords.push(record);
      }

      if (customerReadyCount === null) {
        customerReadyCount = detailRecords.filter((detail) => isCustomerReadyFinding(detail)).length;
      }
      if (evidencePackCount === null) {
        evidencePackCount = detailRecords.filter((detail) => hasDeliverableEvidencePack(detail)).length;
      }
      derivedFromDetail = detailRecords.length > 0;
    }

    const customerCooperationCount = directCustomerInputCount ?? requiredInputs.length;
    const notes = pickArray(metrics.calculation_notes);
    const displayNotes = [
      ...notes,
      "仅展示保守、可解释的交付价值口径，不展示未经 benchmark 证明的发现率或召回率。",
      derivedFromDetail && directCustomerReadyCount === null
        ? "customer-ready finding 数量按“具备脱敏摘要、复现步骤与关闭准则”的证据详情保守推导。"
        : null,
      derivedFromDetail && directEvidencePackCount === null
        ? "证据包数量按可直接交付复核的 evidence bundle / replay 包统计。"
        : null,
    ].filter((item): item is string => Boolean(item));

    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs text-[var(--muted)]">ROI</div>
              <h1 className="mt-2 text-xl font-semibold tracking-tight">ROI / 价值量化</h1>
              <p className="mt-2 text-sm text-[var(--muted)]">
                把后端能力输出转成可决策指标：阻断风险、事故成本区间、节省工时、覆盖率、证据交付成熟度和客户仍需配合的事项。
              </p>
              <div className="mt-3 inline-flex rounded-full border border-[rgba(120,221,255,0.18)] bg-[rgba(76,132,255,0.10)] px-3 py-1 text-xs text-[var(--muted)]">
                可信表达：默认脱敏，仅展示保守口径，不展示夸大性发现率或敏感原文。
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <Link
                  href={`/projects/${p}/risks?launch_blocking=true`}
                  className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.24)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,92,122,0.42)]"
                >
                  下钻上线阻断证据
                </Link>
                <Link
                  href={`/projects/${p}/environment`}
                  className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[rgba(255,196,87,0.24)] bg-[rgba(255,196,87,0.08)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,196,87,0.42)]"
                >
                  去环境诊断
                </Link>
                <Link
                  href={`/projects/${p}/reports/executive`}
                  className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
                >
                  打开领导层报告
                </Link>
              </div>
            </div>
            <Link
              href={`/projects/${p}/reports/executive`}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,255,255,0.18)]"
            >
              返回领导层报告
            </Link>
          </div>
        </div>

        <DecisionSummary projectId={projectId} snapshot={snapshotEnvelope.data} />

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">阻断风险</div>
            <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{blockerCount.toLocaleString()}</div>
            <div className="mt-2 text-sm text-[var(--muted)]">当前仍阻断上线或交付闭环的风险数量</div>
          </div>
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">事故成本区间</div>
            <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">
              {formatCurrencyRange({ min: impactMin, max: impactMax, currency })}
            </div>
            <div className="mt-2 text-sm text-[var(--muted)]">为风险暴露估算区间，不代表确定收益</div>
          </div>
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">节省工时</div>
            <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{savedHours === null ? "—" : `${savedHours} h`}</div>
            <div className="mt-2 text-sm text-[var(--muted)]">按现有价值口径估算，不含人力单价换算</div>
          </div>
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">覆盖率</div>
            <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{formatPercent(coverageRate)}</div>
            <div className="mt-2 text-sm text-[var(--muted)]">
              {coveredFlows !== null && totalFlows !== null ? `关键业务流 ${coveredFlows}/${totalFlows}` : "按业务流覆盖口径展示"}
            </div>
          </div>
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">Customer-ready Finding</div>
            <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">
              {customerReadyCount === null ? "—" : customerReadyCount.toLocaleString()}
            </div>
            <div className="mt-2 text-sm text-[var(--muted)]">具备脱敏摘要、复现步骤和关闭准则的 finding 数量</div>
          </div>
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">证据包数量</div>
            <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">
              {evidencePackCount === null ? "—" : evidencePackCount.toLocaleString()}
            </div>
            <div className="mt-2 text-sm text-[var(--muted)]">可直接交付复核的 replay / evidence bundle 数量</div>
          </div>
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">客户配合项</div>
            <div className="mt-2 text-2xl font-semibold text-[var(--fg)]">{customerCooperationCount.toLocaleString()}</div>
            <div className="mt-2 text-sm text-[var(--muted)]">尚需客户补齐的账号、样例或权限事项</div>
          </div>
        </div>

        <div className="grid gap-3 xl:grid-cols-[1.4fr_1fr]">
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">客户仍需配合项</div>
            <div className="mt-2 text-sm text-[var(--muted)]">这些事项会影响环境准入、证据补齐或交付签收，页面仅展示脱敏摘要。</div>
            <div className="mt-4 grid gap-3">
              {requiredInputs.length ? (
                requiredInputs.slice(0, 4).map((item) => (
                  <div
                    key={item.key}
                    className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] p-4"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="text-sm font-semibold text-[var(--fg)]">{item.title}</div>
                      <div className="text-xs text-[var(--muted)]">
                        {item.priority} / {item.status}
                      </div>
                    </div>
                    <div className="mt-2 text-sm text-[var(--muted)]">{item.whyNeeded}</div>
                    <div className="mt-2 text-sm text-[var(--muted)]">建议客户提供：{item.suggestedInput}</div>
                    <div className="mt-2 text-xs text-[var(--muted)]">
                      影响链路：{item.affectedFlows.length ? item.affectedFlows.join(" / ") : "待补充"}
                    </div>
                  </div>
                ))
              ) : (
                <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] p-4 text-sm text-[var(--muted)]">
                  当前未返回额外客户配合项。
                </div>
              )}
            </div>
            <div className="mt-4">
              <Link
                href={`/projects/${p}/environment`}
                className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,255,255,0.18)]"
              >
                去环境诊断查看详情
              </Link>
            </div>
          </div>

          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">证据可信度与说明</div>
            <div className="mt-3 grid gap-2 text-sm text-[var(--muted)]">
              <div>证据可信度：{trust === null ? "—" : trust}</div>
              <div>脱敏状态：{safeText(metrics.redaction_status ?? environment.redaction_status, "safe")}</div>
            </div>
            <ul className="mt-4 grid gap-2 text-sm text-[var(--muted)]">
              {displayNotes.length ? (
                displayNotes.slice(0, 6).map((item, index) => <li key={`roi-note:${index}:${safeText(item)}`}>{safeText(item)}</li>)
              ) : (
                <li>—</li>
              )}
            </ul>
            <div className="mt-4 flex flex-wrap gap-2">
              <Link
                href={`/projects/${p}/risks?launch_blocking=true`}
                className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
              >
                下钻上线阻断证据
              </Link>
              <Link
                href={`/projects/${p}/risks`}
                className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,255,255,0.18)]"
              >
                查看全部风险证据
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  } catch (err) {
    const safe = toSafeErrorView(err);
    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="text-xs text-[var(--muted)]">ROI</div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">ROI / 价值量化</h1>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="text-sm font-semibold text-[var(--fg)]">{safe.title}</div>
          <div className="mt-2 text-sm text-[var(--muted)]">{safe.detail}</div>
        </div>
      </div>
    );
  }
}
