"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useCommandCenterRealtime } from "@/lib/realtime/command-center";
import { maskId } from "@/lib/redact";
import { buildRuntimeExecutionTheater, isTerminalRuntimeStatus } from "./adapter";

function pickRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function pickString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
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

export function useRuntimeExecutionTheater(input: {
  projectId: string;
  initialSnapshotEnvelope?: unknown;
  initialRunEnvelope?: unknown | null;
}) {
  const { snapshotEnvelope, connection } = useCommandCenterRealtime({
    projectId: input.projectId,
    initialSnapshotEnvelope: input.initialSnapshotEnvelope,
  });

  const [runEnvelope, setRunEnvelope] = useState<unknown | null>(input.initialRunEnvelope ?? null);
  const [overrideRunId, setOverrideRunId] = useState<string | null>(null);
  const snapshotRunId = useMemo(() => {
    const envelope = pickRecord(snapshotEnvelope ?? input.initialSnapshotEnvelope) ?? {};
    const data = pickRecord(envelope.data) ?? {};
    const liveMap = pickRecord(data.live_map) ?? {};
    return pickString(liveMap.run_id);
  }, [input.initialSnapshotEnvelope, snapshotEnvelope]);
  const effectiveOverrideRunId = overrideRunId && snapshotRunId !== overrideRunId ? overrideRunId : null;

  const theater = useMemo(
    () =>
      buildRuntimeExecutionTheater({
        projectId: input.projectId,
        snapshotEnvelope: snapshotEnvelope ?? input.initialSnapshotEnvelope ?? null,
        runEnvelope,
        overrideRunId: effectiveOverrideRunId,
      }),
    [effectiveOverrideRunId, input.initialSnapshotEnvelope, input.projectId, runEnvelope, snapshotEnvelope],
  );

  useEffect(() => {
    if (!theater.runId) return;

    const aborter = new AbortController();
    const url = `/api/ui/projects/${encodeURIComponent(input.projectId)}/test-runs/${encodeURIComponent(theater.runId)}`;

    const load = async () => {
      const next = await fetchJson(url, aborter.signal);
      setRunEnvelope(next);
    };

    void load();

    const timer = setInterval(() => {
      if (!isTerminalRuntimeStatus(theater.runStatus)) void load();
    }, 2500);

    return () => {
      aborter.abort();
      clearInterval(timer);
    };
  }, [input.projectId, theater.runId, theater.runStatus]);

  const startRun = useCallback(
    async (signal: AbortSignal) => {
      const url = `/api/ui/projects/${encodeURIComponent(input.projectId)}/test-runs/start`;
      const env = await postJson(url, {}, signal);
      const envelope = pickRecord(env) ?? {};
      if (envelope.success !== true) {
        const error = pickRecord(envelope.error);
        return { ok: false, detail: pickString(error?.message) ?? "启动失败" };
      }

      const data = pickRecord(envelope.data) ?? {};
      const runId = pickString(data.run_id);
      if (runId) {
        setOverrideRunId(runId);
        setRunEnvelope({
          success: true,
          data: {
            run_id: runId,
            status: "accepted",
            progress: 0,
            probe_total: 0,
            probe_completed: 0,
            probe_failed: 0,
            risk_found: 0,
          },
          error: null,
        });
      }

      return {
        ok: true,
        detail: runId ? `已提交启动请求：${maskId(runId, 6, 4)}` : "已提交启动请求",
      };
    },
    [input.projectId],
  );

  return {
    theater,
    connection,
    startRun,
  };
}
