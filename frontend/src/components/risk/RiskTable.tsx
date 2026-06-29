"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { EvidenceDrawer, type EvidenceDrawerData } from "@/components/evidence/EvidenceDrawer";
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

function safeText(value: unknown, fallback = "—"): string {
  const raw = pickString(value);
  if (!raw) return fallback;
  const redacted = redactUnknown(raw);
  return typeof redacted === "string" ? redacted : fallback;
}

function safeId(value: unknown): string {
  const raw = pickString(value);
  if (!raw) return "unknown";
  return raw.length > 60 ? maskId(raw, 6, 6) : raw;
}

function pickArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

async function fetchJson(url: string): Promise<unknown> {
  const res = await fetch(url, { method: "GET", headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`fetch_failed_${res.status}`);
  return (await res.json()) as unknown;
}

export function RiskTable({ projectId, risks }: { projectId: string; risks: unknown }) {
  const items = useMemo(() => (Array.isArray(risks) ? risks : []), [risks]);
  const p = encodeURIComponent(projectId);
  const [selected, setSelected] = useState<EvidenceDrawerData | null>(null);
  const [loadingRiskId, setLoadingRiskId] = useState<string | null>(null);

  const riskRows = useMemo(
    () =>
      items.map((item) => {
        const obj = pickRecord(item) ?? {};
        const riskId = safeId(obj.risk_id);
        const title = safeText(obj.title);
        const severity = safeText(obj.severity);
        const status = safeText(obj.status, "open");
        const launchBlocking = pickBoolean(obj.launch_blocking);
        const flow = pickRecord(obj.affected_business_flow);
        const flowName = safeText(flow?.name);
        const suggestedAction = safeText(obj.suggested_action);
        const businessImpact = safeText(obj.business_impact);
        return {
          riskId,
          title,
          severity,
          status,
          launchBlocking,
          flowName,
          suggestedAction,
          businessImpact,
        };
      }),
    [items],
  );

  const openRiskDrawer = async (risk: (typeof riskRows)[number]) => {
    setLoadingRiskId(risk.riskId);
    try {
      const envelope = pickRecord(await fetchJson(`/api/ui/projects/${encodeURIComponent(projectId)}/risks/${encodeURIComponent(risk.riskId)}`)) ?? {};
      const data = pickRecord(envelope.data) ?? {};
      const evidence = pickRecord(data.evidence_bundle) ?? {};
      const request = pickRecord(evidence.request_summary) ?? {};
      const response = pickRecord(evidence.response_summary) ?? {};
      const discoveryPath = pickArray(evidence.discovery_path).map((item) => safeText(item)).filter((item) => item !== "—");
      const reproductionSteps = pickArray(evidence.reproduction_steps).map((item) => safeText(item)).filter((item) => item !== "—");
      const summary = safeText(evidence.summary, risk.businessImpact);
      setSelected({
        id: risk.riskId,
        typeLabel: "Risk Evidence",
        title: risk.title,
        summary,
        reason: risk.businessImpact,
        remediationAction: risk.suggestedAction,
        tone: risk.launchBlocking ? "critical" : "warning",
        statusLabel: `${risk.severity} / ${risk.status}`,
        fields: [
          { label: "业务链路", value: risk.flowName },
          { label: "上线阻断", value: risk.launchBlocking === null ? "—" : risk.launchBlocking ? "是" : "否" },
          { label: "请求", value: `${safeText(request.method, "GET")} ${safeText(request.path)}` },
          { label: "响应", value: `${safeText(response.status)} · ${safeText(response.key_signal)}` },
        ],
        sections: [
          { title: "发现路径", items: discoveryPath.length ? discoveryPath : ["暂无发现路径。"] },
          { title: "复现步骤", items: reproductionSteps.length ? reproductionSteps : ["暂无复现步骤。"] },
        ],
        links: [
          { label: "风险详情页", href: `/projects/${p}/risks/${encodeURIComponent(risk.riskId)}`, kind: "page" },
          { label: "Replay 片段", href: `/projects/${p}/risks/${encodeURIComponent(risk.riskId)}#replay`, kind: "artifact" },
          {
            label: "风险详情 JSON",
            href: `/api/ui/projects/${encodeURIComponent(projectId)}/risks/${encodeURIComponent(risk.riskId)}`,
            kind: "artifact",
          },
        ],
      });
    } catch {
      setSelected({
        id: risk.riskId,
        typeLabel: "Risk Evidence",
        title: risk.title,
        summary: risk.businessImpact,
        reason: "风险详情加载失败，当前先展示列表级摘要。",
        remediationAction: risk.suggestedAction,
        tone: risk.launchBlocking ? "critical" : "warning",
        statusLabel: `${risk.severity} / ${risk.status}`,
        fields: [
          { label: "业务链路", value: risk.flowName },
          { label: "上线阻断", value: risk.launchBlocking === null ? "—" : risk.launchBlocking ? "是" : "否" },
        ],
        links: [{ label: "风险详情页", href: `/projects/${p}/risks/${encodeURIComponent(risk.riskId)}`, kind: "page" }],
      });
    } finally {
      setLoadingRiskId(null);
    }
  };

  return (
    <>
      <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)]">
        <div className="grid grid-cols-[1fr_110px_120px_110px] gap-3 border-b border-[var(--border)] bg-[rgba(16,24,38,0.35)] px-5 py-3 text-xs text-[var(--muted)]">
          <div>风险</div>
          <div>严重度</div>
          <div>业务链路</div>
          <div>上线阻断</div>
        </div>
        <div className="divide-y divide-[var(--border)]">
          {riskRows.length ? (
            riskRows.map((risk) => {
              const href = `/projects/${p}/risks/${encodeURIComponent(risk.riskId)}`;
              return (
                <div
                  key={risk.riskId}
                  className="grid grid-cols-[1fr_110px_120px_110px] gap-3 px-5 py-4 text-sm hover:bg-[rgba(255,255,255,0.04)]"
                >
                  <button type="button" onClick={() => void openRiskDrawer(risk)} className="min-w-0 text-left">
                    <div className="truncate text-[var(--fg)]">{risk.title}</div>
                    <div className="mt-1 text-xs text-[var(--muted)]">
                      riskId: {maskId(risk.riskId, 6, 4)}
                      {loadingRiskId === risk.riskId ? " · 正在加载证据…" : ""}
                    </div>
                  </button>
                  <button type="button" onClick={() => void openRiskDrawer(risk)} className="text-left text-[var(--fg)]">
                    {risk.severity}
                  </button>
                  <button type="button" onClick={() => void openRiskDrawer(risk)} className="truncate text-left text-[var(--muted)]">
                    {risk.flowName}
                  </button>
                  <div className="flex items-center justify-between gap-2">
                    <button type="button" onClick={() => void openRiskDrawer(risk)} className="text-left text-[var(--fg)]">
                      {risk.launchBlocking === null ? "—" : risk.launchBlocking ? "是" : "否"}
                    </button>
                    <Link href={href} className="text-xs text-[rgba(122,167,255,0.88)] hover:text-[var(--fg)]">
                      详情页
                    </Link>
                  </div>
                </div>
              );
            })
          ) : (
            <div className="px-5 py-6 text-sm text-[var(--muted)]">暂无风险数据</div>
          )}
        </div>
      </div>
      <EvidenceDrawer open={Boolean(selected)} data={selected} onClose={() => setSelected(null)} />
    </>
  );
}
