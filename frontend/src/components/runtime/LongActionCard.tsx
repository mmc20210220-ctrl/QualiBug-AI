"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export type LongActionOutcome = "success" | "failed" | "canceled";

export interface LongActionArchiveItem {
  id: string;
  title: string;
  outcome: LongActionOutcome;
  startedAt: number;
  endedAt: number;
  detail: string;
}

export interface LongActionTerminalState {
  outcome: Exclude<LongActionOutcome, "canceled">;
  detail: string;
}

export interface LongActionProgress {
  value: number | null;
  label: string;
}

function buildArchiveKey(actionKey: string): string {
  return `qualibug.long_action_archive.${actionKey}`;
}

function loadArchive(actionKey: string): LongActionArchiveItem[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.sessionStorage.getItem(buildArchiveKey(actionKey));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item) => item && typeof item === "object") as LongActionArchiveItem[];
  } catch {
    return [];
  }
}

function saveArchive(actionKey: string, items: LongActionArchiveItem[]) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(buildArchiveKey(actionKey), JSON.stringify(items.slice(0, 8)));
  } catch {}
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  const pad = (v: number) => String(v).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function badgeClasses(outcome: LongActionOutcome): string {
  if (outcome === "success") return "border-[rgba(89,243,194,0.35)] bg-[rgba(89,243,194,0.10)] text-[rgba(196,255,238,0.95)]";
  if (outcome === "canceled") return "border-[rgba(255,210,94,0.35)] bg-[rgba(255,210,94,0.10)] text-[rgba(255,241,203,0.95)]";
  return "border-[rgba(255,86,86,0.35)] bg-[rgba(255,86,86,0.10)] text-[rgba(255,220,220,0.92)]";
}

export function LongActionCard(input: {
  actionKey: string;
  title: string;
  description: string;
  primaryLabel: string;
  danger?: { title: string; detail: string; confirmLabel: string };
  progress?: LongActionProgress;
  terminal?: LongActionTerminalState | null;
  trackTerminal?: boolean;
  run: (signal: AbortSignal) => Promise<{ ok: boolean; detail: string }>;
}) {
  const [phase, setPhase] = useState<"idle" | "confirm" | "running">("idle");
  const [statusText, setStatusText] = useState<string>("");
  const [archive, setArchive] = useState<LongActionArchiveItem[]>(() => loadArchive(input.actionKey));

  const abortRef = useRef<AbortController | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const awaitingTerminalRef = useRef(false);

  const latestTerminal = input.terminal ?? null;
  const trackTerminal = Boolean(input.trackTerminal);

  const canRun = phase !== "running";

  const progressText = useMemo(() => {
    if (!input.progress) return "";
    if (input.progress.value === null) return input.progress.label;
    return `${input.progress.label} · ${Math.round(input.progress.value * 100)}%`;
  }, [input.progress]);

  const pushArchive = (item: LongActionArchiveItem) => {
    const next = [item, ...archive].slice(0, 8);
    setArchive(next);
    saveArchive(input.actionKey, next);
  };

  const finalize = (outcome: LongActionOutcome, detail: string) => {
    const startedAt = startedAtRef.current ?? Date.now();
    const endedAt = Date.now();
    startedAtRef.current = null;
    awaitingTerminalRef.current = false;
    setPhase("idle");
    setStatusText(detail);
    pushArchive({
      id: `${endedAt}-${Math.random().toString(16).slice(2)}`,
      title: input.title,
      outcome,
      startedAt,
      endedAt,
      detail,
    });
  };

  useEffect(() => {
    if (!trackTerminal) return;
    if (!awaitingTerminalRef.current) return;
    if (phase !== "running") return;
    if (!latestTerminal) return;
    finalize(latestTerminal.outcome, latestTerminal.detail);
  }, [latestTerminal, phase, trackTerminal]);

  const run = async () => {
    setStatusText("");
    if (input.danger) {
      setPhase("confirm");
      return;
    }
    startedAtRef.current = Date.now();
    awaitingTerminalRef.current = false;
    setPhase("running");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const result = await input.run(controller.signal);
      if (controller.signal.aborted) {
        finalize("canceled", "已取消");
        return;
      }
      if (!result.ok) {
        finalize("failed", result.detail || "执行失败");
        return;
      }
      if (trackTerminal) {
        awaitingTerminalRef.current = true;
        setStatusText(result.detail || "已提交，等待完成");
        return;
      }
      finalize("success", result.detail || "已完成");
    } catch (err) {
      if (controller.signal.aborted) {
        finalize("canceled", "已取消");
        return;
      }
      finalize("failed", err instanceof Error ? err.message : "执行失败");
    } finally {
      abortRef.current = null;
      if (!trackTerminal) startedAtRef.current = null;
    }
  };

  const cancel = () => {
    if (abortRef.current) abortRef.current.abort();
    if (trackTerminal && awaitingTerminalRef.current) finalize("canceled", "已取消（停止等待）");
  };

  const confirm = () => {
    setPhase("running");
    void (async () => {
      startedAtRef.current = Date.now();
      awaitingTerminalRef.current = false;

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const result = await input.run(controller.signal);
        if (controller.signal.aborted) {
          finalize("canceled", "已取消");
          return;
        }
        if (!result.ok) {
          finalize("failed", result.detail || "执行失败");
          return;
        }
        if (trackTerminal) {
          awaitingTerminalRef.current = true;
          setStatusText(result.detail || "已提交，等待完成");
          return;
        }
        finalize("success", result.detail || "已完成");
      } catch (err) {
        if (controller.signal.aborted) {
          finalize("canceled", "已取消");
          return;
        }
        finalize("failed", err instanceof Error ? err.message : "执行失败");
      } finally {
        abortRef.current = null;
        if (!trackTerminal) startedAtRef.current = null;
      }
    })();
  };

  const closeConfirm = () => {
    setPhase("idle");
  };

  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.40)] p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-xs text-[var(--muted)]">长链路动作</div>
          <div className="mt-1 text-sm font-semibold text-[var(--fg)]">{input.title}</div>
          <div className="mt-1 text-sm text-[var(--muted)]">{input.description}</div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={!canRun}
            onClick={() => void run()}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(89,243,194,0.14)] px-4 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)] disabled:opacity-60"
          >
            {input.primaryLabel}
          </button>
          {phase === "running" ? (
            <button
              type="button"
              onClick={cancel}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,210,94,0.10)] px-4 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
            >
              取消
            </button>
          ) : null}
        </div>
      </div>

      {phase === "running" ? (
        <div className="mt-4 grid gap-2 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.35)] p-4">
          <div className="text-xs text-[var(--muted)]">进度</div>
          <div className="text-sm text-[var(--fg)]">{progressText || statusText || "进行中…"}</div>
          {input.progress && input.progress.value !== null ? (
            <div className="h-2 overflow-hidden rounded bg-[rgba(255,255,255,0.08)]">
              <div
                className="h-full bg-[rgba(89,243,194,0.55)]"
                style={{ width: `${Math.round(Math.max(0, Math.min(1, input.progress.value)) * 100)}%` }}
              />
            </div>
          ) : null}
        </div>
      ) : statusText ? (
        <div className="mt-4 text-sm text-[var(--muted)]">{statusText}</div>
      ) : null}

      {archive.length ? (
        <div className="mt-4 grid gap-2">
          <div className="text-xs text-[var(--muted)]">完成/失败归档（本会话）</div>
          <div className="grid gap-2">
            {archive.slice(0, 5).map((item) => (
              <div
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.30)] px-4 py-3 text-xs text-[var(--muted)]"
              >
                <div className="min-w-0">
                  <div className="truncate text-[var(--fg)]">{item.title}</div>
                  <div className="mt-1 truncate opacity-80">
                    {formatTime(item.startedAt)} → {formatTime(item.endedAt)} · {item.detail}
                  </div>
                </div>
                <div className={`shrink-0 rounded-full border px-2 py-1 ${badgeClasses(item.outcome)}`}>{item.outcome}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {phase === "confirm" && input.danger ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(0,0,0,0.55)] p-4">
          <div className="w-full max-w-[520px] rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(11,15,20,0.98)] p-5 shadow-[var(--shadow-1)]">
            <div className="text-sm font-semibold text-[var(--fg)]">{input.danger.title}</div>
            <div className="mt-2 text-sm text-[var(--muted)]">{input.danger.detail}</div>
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                onClick={closeConfirm}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.45)] px-4 py-2 text-sm text-[var(--muted)] hover:text-[var(--fg)]"
              >
                取消
              </button>
              <button
                type="button"
                onClick={confirm}
                className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,92,122,0.16)] px-4 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
              >
                {input.danger.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

