"use client";

import { useEffect, useMemo, useState } from "react";
import { LongActionCard, type LongActionProgress, type LongActionTerminalState } from "@/components/runtime/LongActionCard";
import { RealtimeConnectionBadge } from "@/components/runtime/RealtimeConnectionBadge";
import { useCommandCenterRealtime } from "@/lib/realtime/command-center";
import { maskId } from "@/lib/redact";

function pickRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function pickString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function pickNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pickArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function isTerminalRunStatus(status: string): boolean {
  const s = status.toLowerCase();
  return s.includes("success") || s.includes("completed") || s.includes("done") || s.includes("failed") || s.includes("error") || s.includes("canceled");
}

function terminalOutcome(status: string): LongActionTerminalState["outcome"] {
  const s = status.toLowerCase();
  if (s.includes("fail") || s.includes("error")) return "failed";
  return "success";
}

async function fetchJson(url: string, signal: AbortSignal): Promise<unknown> {
  const res = await fetch(url, { method: "GET", signal, headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`fetch_failed_${res.status}`);
  return (await res.json()) as unknown;
}

async function postJson(url: string, body: unknown, signal: AbortSignal): Promise<unknown> {
  const res = await fetch(url, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`post_failed_${res.status}`);
  return (await res.json()) as unknown;
}

export function ExecutionRealtimePanel(input: { projectId: string; initialSnapshotEnvelope?: unknown }) {
  const { snapshotEnvelope, connection } = useCommandCenterRealtime({
    projectId: input.projectId,
    initialSnapshotEnvelope: input.initialSnapshotEnvelope,
  });

  const snapshot = useMemo(() => {
    const env = pickRecord(snapshotEnvelope) ?? {};
    return pickRecord(env.data) ?? {};
  }, [snapshotEnvelope]);

  const liveMap = useMemo(() => pickRecord(snapshot.live_map) ?? {}, [snapshot.live_map]);
  const runId = useMemo(() => pickString(liveMap.run_id), [liveMap.run_id]);
  const recentEvents = useMemo(() => pickArray(snapshot.recent_events), [snapshot.recent_events]);

  const [runEnvelope, setRunEnvelope] = useState<unknown | null>(null);
  const run = useMemo(() => {
    const env = pickRecord(runEnvelope) ?? {};
    return pickRecord(env.data);
  }, [runEnvelope]);

  const runStatus = useMemo(() => pickString(run?.status) ?? "", [run?.status]);
  const runProgressRaw = useMemo(() => pickNumber(run?.progress), [run?.progress]);
  const runProgress: LongActionProgress | undefined = useMemo(() => {
    if (!runId) return undefined;
    if (runProgressRaw === null) return { value: null, label: "等待进度…" };
    return { value: Math.max(0, Math.min(1, runProgressRaw)), label: "运行中" };
  }, [runId, runProgressRaw]);

  const runTerminal: LongActionTerminalState | null = useMemo(() => {
    if (!runStatus) return null;
    if (!isTerminalRunStatus(runStatus)) return null;
    return { outcome: terminalOutcome(runStatus), detail: `运行已结束：${runStatus}` };
  }, [runStatus]);

  useEffect(() => {
    if (!runId) {
      setRunEnvelope(null);
      return;
    }

    const aborter = new AbortController();
    const url = `/api/ui/projects/${encodeURIComponent(input.projectId)}/test-runs/${encodeURIComponent(runId)}`;

    const load = async () => {
      const env = await fetchJson(url, aborter.signal);
      setRunEnvelope(env);
    };

    void load();

    const timer = setInterval(() => {
      if (!runStatus || !isTerminalRunStatus(runStatus)) void load();
    }, 2500);

    return () => {
      aborter.abort();
      clearInterval(timer);
    };
  }, [input.projectId, runId, runStatus]);

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] p-4">
        <div className="text-sm text-[var(--fg)]">实时状态</div>
        <RealtimeConnectionBadge state={connection} />
      </div>

      <LongActionCard
        actionKey={`project.${input.projectId}.start_test_run`}
        title="启动测试运行"
        description="统一交互：危险确认 → 启动 → 实时进度 → 完成/失败归档（取消仅停止等待，不保证后端中止）。"
        primaryLabel="启动测试"
        danger={{
          title: "确认启动测试运行？",
          detail: "该操作可能消耗算力与环境资源，并会产生新的运行记录。请确认当前项目处于可执行状态。",
          confirmLabel: "确认启动",
        }}
        trackTerminal
        progress={runProgress}
        terminal={runTerminal}
        run={async (signal) => {
          const url = `/api/ui/projects/${encodeURIComponent(input.projectId)}/test-runs/start`;
          const env = await postJson(url, {}, signal);
          const envelope = pickRecord(env) ?? {};
          if (envelope.success !== true) {
            const err = pickRecord(envelope.error);
            const msg = err && typeof err.message === "string" ? err.message : "启动失败";
            return { ok: false, detail: msg };
          }
          const data = pickRecord(envelope.data) ?? {};
          const rid = pickString(data.run_id) ?? runId ?? "";
          return { ok: true, detail: rid ? `已提交启动请求：${maskId(rid, 6, 4)}` : "已提交启动请求" };
        }}
      />

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs text-[var(--muted)]">运行概览（实时）</div>
            <div className="mt-1 text-sm text-[var(--muted)]">runId：{runId ? maskId(runId, 6, 4) : "—"}</div>
          </div>
          {runStatus ? (
            <div className="rounded-full border border-[var(--border)] bg-[rgba(89,243,194,0.10)] px-3 py-1 text-xs text-[var(--fg)]">
              {runStatus}
            </div>
          ) : (
            <div className="rounded-full border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-3 py-1 text-xs text-[var(--muted)]">
              无运行
            </div>
          )}
        </div>

        {run ? (
          <div className="mt-3 grid gap-2 text-sm text-[var(--muted)] md:grid-cols-2">
            <div>进度：{runProgressRaw === null ? "—" : `${Math.round(Math.max(0, Math.min(1, runProgressRaw)) * 100)}%`}</div>
            <div>
              探针：{String(run.probe_completed ?? "—")}/{String(run.probe_total ?? "—")} · 失败 {String(run.probe_failed ?? "—")}
            </div>
            <div>发现风险：{String(run.risk_found ?? "—")}</div>
            <div>状态：{runStatus || "—"}</div>
          </div>
        ) : (
          <div className="mt-3 text-sm text-[var(--muted)]">暂无运行数据</div>
        )}
      </div>

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
        <div className="text-xs text-[var(--muted)]">事件流（实时）</div>
        <div className="mt-3 grid gap-2">
          {recentEvents.length ? (
            recentEvents.slice(0, 10).map((item, idx) => {
              const evt = pickRecord(item) ?? {};
              const msg = pickString(evt.message) ?? "—";
              const ts = pickString(evt.timestamp) ?? "";
              return (
                <div
                  key={`${idx}-${ts}-${msg}`}
                  className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] px-4 py-3 text-sm text-[var(--muted)]"
                >
                  <div className="text-[var(--fg)]">{msg}</div>
                  <div className="mt-1 text-xs text-[var(--muted)]">{ts}</div>
                </div>
              );
            })
          ) : (
            <div className="text-sm text-[var(--muted)]">暂无事件</div>
          )}
        </div>
      </div>
    </div>
  );
}

