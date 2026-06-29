import Link from "next/link";
import { RiskReplayTimelinePanel } from "@/components/risk/RiskReplayTimeline";
import { DecisionSummary } from "@/components/value-summary/DecisionSummary";
import { buildRiskReplayTimeline } from "@/features/risk-replay/adapter";
import { getCommandCenterSnapshot, getRiskDetail, toSafeErrorView } from "@/lib/api/command-center";
import { redactUnknown, maskId } from "@/lib/redact";

function pickRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function pickString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function pickBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function pickNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pickArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function safeText(value: unknown, fallback = "—"): string {
  const raw = pickString(value);
  if (!raw) return fallback;
  const redacted = redactUnknown(raw);
  return typeof redacted === "string" ? redacted : fallback;
}

export default async function RiskEvidenceDetailPage({
  params,
}: {
  params: Promise<{ projectId: string; riskId: string }>;
}) {
  const { projectId, riskId } = await params;
  const p = encodeURIComponent(projectId);

  try {
    const [snapshotEnvelope, detailEnvelope] = await Promise.all([
      getCommandCenterSnapshot(projectId),
      getRiskDetail(projectId, riskId),
    ]);

    const detail = pickRecord(detailEnvelope.data) ?? {};
    const risk = pickRecord(detail.risk) ?? {};
    const evidence = pickRecord(detail.evidence_bundle) ?? {};
    const flow = pickRecord(risk.affected_business_flow) ?? {};

    const title = safeText(risk.title, riskId);
    const severity = safeText(risk.severity);
    const launchBlocking = pickBoolean(risk.launch_blocking);
    const impact = safeText(risk.business_impact);
    const suggested = safeText(risk.suggested_action);
    const evidenceScore = pickNumber(risk.evidence_score);
    const reproducibility = pickNumber(risk.reproducibility_score);

    const request = pickRecord(evidence.request_summary) ?? {};
    const response = pickRecord(evidence.response_summary) ?? {};
    const suggestedFix = safeText(evidence.suggested_fix);
    const closure = pickArray(evidence.closure_criteria);
    const replayTimeline = buildRiskReplayTimeline({
      projectId,
      riskId,
      detail,
      snapshot: snapshotEnvelope.data,
    });

    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-xs text-[var(--muted)]">Finding / 风险详情</div>
              <h1 className="mt-2 truncate text-xl font-semibold tracking-tight">{title}</h1>
              <div className="mt-2 flex flex-wrap gap-2 text-xs text-[var(--muted)]">
                <span>riskId {maskId(String(risk.risk_id ?? riskId), 6, 4)}</span>
                <span>严重度 {severity}</span>
                <span>链路 {safeText(flow.name, "未归属")}</span>
                <span>上线阻断 {launchBlocking === null ? "—" : launchBlocking ? "是" : "否"}</span>
              </div>
            </div>
            <Link
              href={`/projects/${p}/risks`}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,255,255,0.18)]"
            >
              返回列表
            </Link>
          </div>
        </div>

        <DecisionSummary projectId={projectId} snapshot={snapshotEnvelope.data} />

        <div className="grid gap-3 xl:grid-cols-3">
          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">业务影响</div>
            <div className="mt-3 text-sm text-[var(--fg)]">{impact}</div>
            <div className="mt-4 grid gap-2 text-xs text-[var(--muted)]">
              <div>证据强度 {evidenceScore === null ? "—" : evidenceScore}</div>
              <div>可复现度 {reproducibility === null ? "—" : reproducibility}</div>
            </div>
          </div>

          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">修复建议</div>
            <div className="mt-3 text-sm text-[var(--fg)]">{suggested}</div>
            <div className="mt-4 text-xs text-[var(--muted)]">关闭准则来自后端规则（默认已脱敏）。</div>
            <ul className="mt-2 grid gap-2 text-sm text-[var(--muted)]">
              {closure.length ? closure.slice(0, 6).map((item, index) => <li key={`closure:${index}:${safeText(item)}`}>{safeText(item)}</li>) : <li>—</li>}
            </ul>
          </div>

          <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
            <div className="text-xs text-[var(--muted)]">Replay Readiness</div>
            <div className="mt-3 grid gap-3">
              <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] p-4">
                <div className="text-xs text-[var(--muted)]">回放步骤</div>
                <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{replayTimeline.metrics.totalSteps}</div>
              </div>
              <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] p-4">
                <div className="text-xs text-[var(--muted)]">失败落点</div>
                <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{replayTimeline.metrics.failureSteps}</div>
              </div>
              <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] p-4">
                <div className="text-xs text-[var(--muted)]">结构化复现</div>
                <div className="mt-2 text-sm font-semibold text-[var(--fg)]">
                  {replayTimeline.metrics.replayReady ? "已就绪" : "待补充"}
                </div>
              </div>
            </div>
          </div>
        </div>

        <RiskReplayTimelinePanel timeline={replayTimeline} />

        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="text-xs text-[var(--muted)]">证据链摘要（严格脱敏）</div>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] p-4">
              <div className="text-xs text-[var(--muted)]">请求摘要</div>
              <div className="mt-2 grid gap-1 text-sm text-[var(--muted)]">
                <div>
                  {safeText(request.method, "GET")} {safeText(request.path)}
                </div>
                <div>身份上下文：{safeText(request.auth_context)}</div>
              </div>
            </div>
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] p-4">
              <div className="text-xs text-[var(--muted)]">响应摘要</div>
              <div className="mt-2 grid gap-1 text-sm text-[var(--muted)]">
                <div>状态码：{safeText(response.status_code ?? response.status)}</div>
                <div>观察到的问题：{safeText(response.observed_issue ?? response.key_signal)}</div>
              </div>
            </div>
          </div>

          <div className="mt-4 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] p-4">
            <div className="text-xs text-[var(--muted)]">建议修复路径</div>
            <div className="mt-2 text-sm text-[var(--muted)]">{suggestedFix}</div>
          </div>
        </div>
      </div>
    );
  } catch (err) {
    const safe = toSafeErrorView(err);
    return (
      <div className="grid gap-4">
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
          <div className="text-xs text-[var(--muted)]">风险详情</div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight">{riskId}</h1>
        </div>
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
          <div className="text-sm font-semibold text-[var(--fg)]">{safe.title}</div>
          <div className="mt-2 text-sm text-[var(--muted)]">{safe.detail}</div>
          <div className="mt-4">
            <Link
              href={`/projects/${p}/risks`}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-2 text-sm hover:border-[rgba(255,255,255,0.18)]"
            >
              返回列表
            </Link>
          </div>
        </div>
      </div>
    );
  }
}
