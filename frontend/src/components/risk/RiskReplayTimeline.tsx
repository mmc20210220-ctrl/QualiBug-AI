"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { RiskReplayStep, RiskReplayStepStatus, RiskReplayTimeline } from "@/features/risk-replay/model";

function statusLabel(status: RiskReplayStepStatus): string {
  if (status === "running") return "播放中";
  if (status === "completed") return "已完成";
  if (status === "warning") return "风险信号";
  if (status === "failed") return "失败点";
  if (status === "blocked") return "阻断点";
  return "待执行";
}

function statusClasses(status: RiskReplayStepStatus): string {
  if (status === "failed" || status === "blocked") {
    return "border-[rgba(255,92,122,0.28)] bg-[rgba(255,92,122,0.12)] text-[rgba(255,228,234,0.96)]";
  }
  if (status === "warning") {
    return "border-[rgba(245,191,80,0.26)] bg-[rgba(245,191,80,0.12)] text-[rgba(255,242,216,0.96)]";
  }
  if (status === "completed") {
    return "border-[rgba(89,243,194,0.28)] bg-[rgba(89,243,194,0.12)] text-[rgba(220,255,245,0.96)]";
  }
  if (status === "running") {
    return "border-[rgba(122,167,255,0.28)] bg-[rgba(122,167,255,0.12)] text-[rgba(230,239,255,0.96)]";
  }
  return "border-[var(--border)] bg-[rgba(255,255,255,0.04)] text-[var(--muted)]";
}

function formatTimestamp(value: string | undefined): string {
  if (!value) return "时间待补充";
  return value.replace("T", " ").replace("Z", "");
}

function stepCardClasses(step: RiskReplayStep, active: boolean): string {
  const activeRing = active ? "border-[rgba(122,167,255,0.42)] shadow-[0_0_0_1px_rgba(122,167,255,0.24)]" : "border-[var(--border)]";
  const tone =
    step.status === "failed" || step.status === "blocked"
      ? "bg-[rgba(255,92,122,0.08)]"
      : step.status === "warning"
        ? "bg-[rgba(245,191,80,0.08)]"
        : active
          ? "bg-[rgba(122,167,255,0.08)]"
          : "bg-[rgba(16,24,38,0.34)]";
  return `rounded-[var(--radius-sm)] border px-4 py-4 text-left transition hover:border-[rgba(255,255,255,0.18)] ${activeRing} ${tone}`;
}

export function RiskReplayTimelinePanel({ timeline }: { timeline: RiskReplayTimeline }) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "done" | "failed">("idle");
  const stepRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const failureIndex = useMemo(
    () => timeline.steps.findIndex((step) => step.failurePoint || step.status === "failed" || step.status === "blocked"),
    [timeline.steps],
  );
  const activeStep = timeline.steps[activeIndex] ?? null;
  const progressPercent = timeline.steps.length ? Math.round(((activeIndex + 1) / timeline.steps.length) * 100) : 0;

  useEffect(() => {
    setActiveIndex(0);
    setIsPlaying(false);
    setCopyState("idle");
  }, [timeline.riskId]);

  useEffect(() => {
    if (!isPlaying || timeline.steps.length <= 1) return undefined;
    const timer = window.setInterval(() => {
      setActiveIndex((current) => {
        if (current >= timeline.steps.length - 1) {
          setIsPlaying(false);
          return current;
        }
        return current + 1;
      });
    }, 1400);
    return () => window.clearInterval(timer);
  }, [isPlaying, timeline.steps.length]);

  useEffect(() => {
    const target = stepRefs.current[activeIndex];
    target?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeIndex]);

  const handleCopy = async () => {
    if (!timeline.copyText) return;
    try {
      await navigator.clipboard.writeText(timeline.copyText);
      setCopyState("done");
    } catch {
      setCopyState("failed");
    }
  };

  return (
    <section id="replay" className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(14,22,34,0.44)] p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs text-[var(--muted)]">Replay Timeline</div>
          <h2 className="mt-2 text-lg font-semibold text-[var(--fg)]">Finding / 风险证据回放</h2>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{timeline.summary}</p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <span className={`rounded-full border px-3 py-1 ${statusClasses(timeline.status)}`}>{statusLabel(timeline.status)}</span>
          <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.04)] px-3 py-1 text-[var(--muted)]">
            {timeline.metrics.totalSteps} steps
          </span>
          <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.04)] px-3 py-1 text-[var(--muted)]">
            更新时间 {formatTimestamp(timeline.updatedAt)}
          </span>
        </div>
      </div>

      <div className="mt-4 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.34)] p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm text-[var(--fg)]">
            当前帧 {Math.min(activeIndex + 1, Math.max(timeline.steps.length, 1))}/{Math.max(timeline.steps.length, 1)}
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setIsPlaying((value) => !value)}
              disabled={timeline.steps.length <= 1}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isPlaying ? "暂停" : "播放"}
            </button>
            <button
              type="button"
              onClick={() => setActiveIndex(0)}
              disabled={!timeline.steps.length}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              回到起点
            </button>
            <button
              type="button"
              onClick={() => {
                if (failureIndex >= 0) {
                  setActiveIndex(failureIndex);
                  setIsPlaying(false);
                }
              }}
              disabled={failureIndex < 0}
              className="rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.24)] bg-[rgba(255,92,122,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,92,122,0.42)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              跳转失败点
            </button>
            <button
              type="button"
              onClick={handleCopy}
              disabled={!timeline.reproductionSteps.length}
              className="rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.24)] bg-[rgba(122,167,255,0.10)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(122,167,255,0.42)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              复制复现步骤
            </button>
          </div>
        </div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-[rgba(255,255,255,0.06)]">
          <div
            className="h-full rounded-full bg-[linear-gradient(90deg,rgba(122,167,255,0.92),rgba(89,243,194,0.92))]"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
        <div className="mt-2 text-xs text-[var(--muted)]">
          {copyState === "done"
            ? "复现步骤已复制。"
            : copyState === "failed"
              ? "复制失败，请手动复制。"
              : "支持逐帧浏览、自动播放与失败点定位。"}
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_340px]">
        <div className="grid max-h-[620px] gap-3 overflow-y-auto pr-1">
          {timeline.steps.length ? (
            timeline.steps.map((step, index) => {
              const active = index === activeIndex;
              return (
                <button
                  key={step.stepId}
                  ref={(node) => {
                    stepRefs.current[index] = node;
                  }}
                  type="button"
                  onClick={() => {
                    setActiveIndex(index);
                    setIsPlaying(false);
                  }}
                  className={stepCardClasses(step, active)}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-xs text-[var(--muted)]">
                        Step {index + 1} · {step.kind}
                      </div>
                      <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{step.title}</div>
                      <div className="mt-2 text-sm leading-6 text-[var(--muted)]">{step.summary}</div>
                    </div>
                    <span className={`rounded-full border px-3 py-1 text-xs ${statusClasses(step.status)}`}>{statusLabel(step.status)}</span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--muted)]">
                    <span>{formatTimestamp(step.timestamp)}</span>
                    {step.cue ? <span>{step.cue}</span> : null}
                    {step.failurePoint ? <span>可定位失败点</span> : null}
                  </div>
                </button>
              );
            })
          ) : (
            <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.34)] px-4 py-5 text-sm text-[var(--muted)]">
              暂无可播放步骤，当前仍保留原有风险摘要视图。
            </div>
          )}
        </div>

        <aside className="grid gap-4">
          <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.34)] p-4">
            <div className="text-xs text-[var(--muted)]">当前帧详情</div>
            {activeStep ? (
              <>
                <div className="mt-2 text-sm font-semibold text-[var(--fg)]">{activeStep.title}</div>
                <div className="mt-2 text-sm leading-6 text-[var(--muted)]">{activeStep.summary}</div>
                <div className="mt-3 flex flex-wrap gap-2 text-xs">
                  <span className={`rounded-full border px-3 py-1 ${statusClasses(activeStep.status)}`}>{statusLabel(activeStep.status)}</span>
                  <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.04)] px-3 py-1 text-[var(--muted)]">
                    {formatTimestamp(activeStep.timestamp)}
                  </span>
                </div>
                {activeStep.fields.length ? (
                  <div className="mt-4 grid gap-2">
                    {activeStep.fields.map((field) => (
                      <div key={`${activeStep.stepId}-${field.label}-${field.value}`} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-3">
                        <div className="text-xs text-[var(--muted)]">{field.label}</div>
                        <div className="mt-1 text-sm text-[var(--fg)]">{field.value}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
              </>
            ) : (
              <div className="mt-2 text-sm text-[var(--muted)]">选择一个步骤查看详情。</div>
            )}
          </div>

          <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.34)] p-4">
            <div className="text-xs text-[var(--muted)]">失败跳点</div>
            <div className="mt-3 grid gap-2">
              {timeline.jumpTargets.length ? (
                timeline.jumpTargets.map((target) => {
                  const targetIndex = timeline.steps.findIndex((step) => step.stepId === target.stepId);
                  return (
                    <button
                      key={target.stepId}
                      type="button"
                      onClick={() => {
                        if (targetIndex >= 0) {
                          setActiveIndex(targetIndex);
                          setIsPlaying(false);
                        }
                      }}
                      className="rounded-[var(--radius-sm)] border border-[rgba(255,92,122,0.22)] bg-[rgba(255,92,122,0.08)] px-3 py-3 text-left text-sm text-[var(--fg)] hover:border-[rgba(255,92,122,0.42)]"
                    >
                      <div>{target.label}</div>
                      <div className="mt-1 text-xs text-[var(--muted)]">{target.reason}</div>
                    </button>
                  );
                })
              ) : (
                <div className="text-sm text-[var(--muted)]">当前没有显式失败点，说明该风险更多是异常信号而非硬阻断。</div>
              )}
            </div>
          </div>

          <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.34)] p-4">
            <div className="flex items-center justify-between gap-3">
              <div className="text-xs text-[var(--muted)]">复现步骤包</div>
              <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.04)] px-3 py-1 text-xs text-[var(--muted)]">
                {timeline.reproductionSteps.length} steps
              </span>
            </div>
            <ul className="mt-3 grid gap-2 text-sm leading-6 text-[var(--muted)]">
              {timeline.reproductionSteps.length ? (
                timeline.reproductionSteps.map((step, index) => <li key={`${timeline.riskId}-${index + 1}-${step}`}>{index + 1}. {step}</li>)
              ) : (
                <li>暂无结构化复现步骤。</li>
              )}
            </ul>
          </div>
        </aside>
      </div>
    </section>
  );
}
