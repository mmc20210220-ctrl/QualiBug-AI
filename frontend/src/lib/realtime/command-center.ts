import { useEffect, useMemo, useRef, useState } from "react";
import { readRealtimePolicy, type RealtimeMode } from "./policy";

export type RealtimeTransport = "sse" | "poll" | "ws";
export type RealtimePhase = "idle" | "connecting" | "open" | "degraded" | "error" | "closed";

export interface RealtimeConnectionState {
  mode: RealtimeMode;
  transport: RealtimeTransport;
  phase: RealtimePhase;
  detail: string;
  lastMessageAt: number | null;
}

export interface CommandCenterRealtimeState {
  snapshotEnvelope: unknown | null;
  connection: RealtimeConnectionState;
}

function now(): number {
  return Date.now();
}

function buildSnapshotUrl(projectId: string): string {
  return `/api/ui/projects/${encodeURIComponent(projectId)}/command-center/snapshot`;
}

function buildStreamUrl(projectId: string): string {
  return `/api/ui/projects/${encodeURIComponent(projectId)}/command-center/stream`;
}

function createState(input: Partial<RealtimeConnectionState>): RealtimeConnectionState {
  return {
    mode: input.mode ?? "auto",
    transport: input.transport ?? "poll",
    phase: input.phase ?? "idle",
    detail: input.detail ?? "",
    lastMessageAt: input.lastMessageAt ?? null,
  };
}

async function fetchSnapshot(url: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(url, { method: "GET", signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`snapshot_failed_${response.status}`);
  return (await response.json()) as unknown;
}

export function useCommandCenterRealtime(input: {
  projectId: string;
  initialSnapshotEnvelope?: unknown;
}): CommandCenterRealtimeState {
  const policy = useMemo(() => readRealtimePolicy(), []);
  const snapshotUrl = useMemo(() => buildSnapshotUrl(input.projectId), [input.projectId]);
  const streamUrl = useMemo(() => buildStreamUrl(input.projectId), [input.projectId]);

  const [snapshotEnvelope, setSnapshotEnvelope] = useState<unknown | null>(input.initialSnapshotEnvelope ?? null);
  const [connection, setConnection] = useState<RealtimeConnectionState>(() =>
    createState({ mode: policy.mode, transport: "poll", phase: "idle", detail: "" }),
  );

  const lastEnvelopeRef = useRef<unknown | null>(snapshotEnvelope);
  useEffect(() => {
    lastEnvelopeRef.current = snapshotEnvelope;
  }, [snapshotEnvelope]);

  const stoppedRef = useRef(false);
  useEffect(() => {
    stoppedRef.current = false;
    return () => {
      stoppedRef.current = true;
    };
  }, []);

  useEffect(() => {
    if (policy.mode === "ws") {
      setConnection(
        createState({
          mode: policy.mode,
          transport: "ws",
          phase: "degraded",
          detail: "WebSocket 占位：当前构建未启用 WS 服务端",
        }),
      );
      return;
    }

    const aborter = new AbortController();
    let pollingTimer: ReturnType<typeof setInterval> | null = null;
    let fallbackTimer: ReturnType<typeof setTimeout> | null = null;
    let eventSource: EventSource | null = null;

    const stopAll = () => {
      if (fallbackTimer) clearTimeout(fallbackTimer);
      if (pollingTimer) clearInterval(pollingTimer);
      fallbackTimer = null;
      pollingTimer = null;
      if (eventSource) eventSource.close();
      eventSource = null;
      aborter.abort();
    };

    const startPolling = (reason: string) => {
      if (stoppedRef.current) return;
      stopAll();
      setConnection(
        createState({
          mode: policy.mode,
          transport: "poll",
          phase: reason ? "degraded" : "connecting",
          detail: reason,
        }),
      );

      const pollOnce = async () => {
        try {
          const envelope = await fetchSnapshot(snapshotUrl, aborter.signal);
          if (stoppedRef.current) return;
          setSnapshotEnvelope(envelope);
          setConnection((prev) =>
            createState({
              ...prev,
              mode: policy.mode,
              transport: "poll",
              phase: "open",
              detail: reason,
              lastMessageAt: now(),
            }),
          );
        } catch (err) {
          if (stoppedRef.current) return;
          if (aborter.signal.aborted) return;
          const detail = err instanceof Error ? err.message : "poll_failed";
          setConnection((prev) =>
            createState({
              ...prev,
              mode: policy.mode,
              transport: "poll",
              phase: "error",
              detail,
            }),
          );
        }
      };

      void pollOnce();
      pollingTimer = setInterval(() => {
        void pollOnce();
      }, policy.pollIntervalMs);
    };

    const startSse = () => {
      if (stoppedRef.current) return;
      setConnection(createState({ mode: policy.mode, transport: "sse", phase: "connecting", detail: "" }));

      fallbackTimer = setTimeout(() => {
        if (stoppedRef.current) return;
        startPolling("SSE 连接超时，已降级到轮询");
      }, policy.sseOpenTimeoutMs);

      try {
        eventSource = new EventSource(streamUrl);
      } catch (err) {
        startPolling("SSE 初始化失败，已降级到轮询");
        return;
      }

      eventSource.addEventListener("open", () => {
        if (fallbackTimer) clearTimeout(fallbackTimer);
        fallbackTimer = null;
        if (stoppedRef.current) return;
        setConnection((prev) => createState({ ...prev, mode: policy.mode, transport: "sse", phase: "open", detail: "" }));
      });

      const onSnapshot = (evt: MessageEvent) => {
        if (stoppedRef.current) return;
        if (typeof evt.data !== "string") return;
        try {
          const payload = JSON.parse(evt.data) as unknown;
          setSnapshotEnvelope(payload);
          setConnection((prev) =>
            createState({
              ...prev,
              mode: policy.mode,
              transport: "sse",
              phase: "open",
              detail: "",
              lastMessageAt: now(),
            }),
          );
        } catch {
          setConnection((prev) =>
            createState({
              ...prev,
              mode: policy.mode,
              transport: "sse",
              phase: "degraded",
              detail: "SSE 收到不可解析数据",
            }),
          );
        }
      };

      eventSource.addEventListener("snapshot", onSnapshot as EventListener);

      eventSource.addEventListener("error", () => {
        if (stoppedRef.current) return;
        if (policy.mode === "sse") {
          setConnection((prev) =>
            createState({
              ...prev,
              mode: policy.mode,
              transport: "sse",
              phase: "error",
              detail: "SSE 连接异常",
            }),
          );
          return;
        }
        startPolling("SSE 连接异常，已降级到轮询");
      });
    };

    if (policy.mode === "poll") startPolling("");
    else startSse();

    return () => {
      stopAll();
    };
  }, [policy.mode, policy.pollIntervalMs, policy.sseOpenTimeoutMs, snapshotUrl, streamUrl]);

  return { snapshotEnvelope, connection };
}
