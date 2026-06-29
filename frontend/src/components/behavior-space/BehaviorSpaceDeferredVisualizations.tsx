"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import type { BehaviorSpaceVisualization } from "@/behavior-space/types";

const LazyBehaviorSpaceFlow = dynamic(
  () => import("./BehaviorSpaceFlow").then((module) => module.BehaviorSpaceFlow),
  {
    ssr: false,
    loading: () => <FlowSkeleton />,
  },
);

const LazyBehaviorSpaceSandbox = dynamic(
  () => import("./BehaviorSpaceSandbox").then((module) => module.BehaviorSpaceSandbox),
  {
    ssr: false,
    loading: () => <ShowcaseSkeleton compact={false} />,
  },
);

type IdleWindow = Window & {
  requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
  cancelIdleCallback?: (handle: number) => void;
};

function SkeletonBlock({ className }: { className: string }) {
  return <div className={`animate-pulse rounded-[var(--radius-sm)] bg-[rgba(255,255,255,0.06)] ${className}`} />;
}

function ShowcaseSkeleton({ compact }: { compact: boolean }) {
  return (
    <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[linear-gradient(180deg,rgba(8,14,24,0.96),rgba(12,21,35,0.88))] p-5 shadow-[var(--shadow-1)]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <div className="text-xs uppercase tracking-[0.24em] text-[var(--muted)]">2.5D Showcase</div>
          <div className="mt-2 text-lg font-semibold text-[var(--fg)]">旗舰演示层准备中</div>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
            首屏已优先返回上线建议、风险成本与审计/回放入口，演示层将在你滚动到此处或空闲时再加载。
          </p>
        </div>
        <div className="rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.22)] bg-[rgba(122,167,255,0.08)] px-3 py-2 text-xs text-[var(--muted)]">
          按需加载以降低首屏阻塞
        </div>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={`showcase-metric:${index}`} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-3">
            <SkeletonBlock className="h-3 w-16" />
            <SkeletonBlock className="mt-3 h-5 w-28" />
          </div>
        ))}
      </div>
      {!compact ? (
        <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-h-[420px] rounded-[20px] border border-[var(--border)] bg-[radial-gradient(circle_at_top,rgba(122,167,255,0.12),rgba(6,10,18,0.94)_62%)] p-4">
            <SkeletonBlock className="h-full min-h-[388px] w-full rounded-[16px]" />
          </div>
          <div className="rounded-[20px] border border-[var(--border)] bg-[rgba(11,18,29,0.76)] p-4">
            <SkeletonBlock className="h-3 w-24" />
            <SkeletonBlock className="mt-3 h-6 w-40" />
            <SkeletonBlock className="mt-3 h-4 w-5/6" />
            <div className="mt-4 grid gap-2">
              {Array.from({ length: 3 }).map((_, index) => (
                <SkeletonBlock key={`showcase-side:${index}`} className="h-12 w-full" />
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function FlowSkeleton() {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-h-[760px] overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(9,14,22,0.86)] p-4">
        <div className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(11,15,20,0.88)] px-4 py-3 text-xs text-[var(--muted)] shadow-[var(--shadow-1)]">
          图布局、连线与交互面板正在初始化
        </div>
        <div className="mt-4 grid h-[660px] place-items-center rounded-[var(--radius-sm)] border border-dashed border-[rgba(122,167,255,0.18)] bg-[rgba(255,255,255,0.02)]">
          <div className="grid gap-3">
            <SkeletonBlock className="h-16 w-56" />
            <SkeletonBlock className="h-16 w-48" />
            <SkeletonBlock className="h-16 w-64" />
          </div>
        </div>
      </div>
      <aside className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-5 shadow-[var(--shadow-1)] backdrop-blur">
        <SkeletonBlock className="h-3 w-16" />
        <SkeletonBlock className="mt-4 h-6 w-36" />
        <SkeletonBlock className="mt-4 h-4 w-full" />
        <SkeletonBlock className="mt-2 h-4 w-5/6" />
        <div className="mt-4 grid gap-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <SkeletonBlock key={`flow-side:${index}`} className="h-14 w-full" />
          ))}
        </div>
      </aside>
    </div>
  );
}

function DeferredSection({
  sectionId,
  title,
  description,
  renderContent,
  placeholder,
  preloadOnIdle,
}: {
  sectionId: string;
  title: string;
  description: string;
  renderContent: () => React.ReactNode;
  placeholder: React.ReactNode;
  preloadOnIdle: boolean;
}) {
  const sectionRef = useRef<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (ready) return undefined;
    const current = sectionRef.current;
    if (!current) return undefined;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setReady(true);
        }
      },
      { rootMargin: "320px 0px" },
    );

    observer.observe(current);
    return () => observer.disconnect();
  }, [ready]);

  useEffect(() => {
    if (!preloadOnIdle || ready) return undefined;

    const idleWindow = window as IdleWindow;
    if (idleWindow.requestIdleCallback) {
      const handle = idleWindow.requestIdleCallback(() => setReady(true), { timeout: 1600 });
      return () => idleWindow.cancelIdleCallback?.(handle);
    }

    const timer = window.setTimeout(() => setReady(true), 1200);
    return () => window.clearTimeout(timer);
  }, [preloadOnIdle, ready]);

  return (
    <div id={sectionId} ref={sectionRef}>
      {ready ? (
        renderContent()
      ) : (
        <div className="grid gap-3">
          <div className="flex flex-wrap items-start justify-between gap-3 rounded-[var(--radius-md)] border border-[rgba(122,167,255,0.14)] bg-[rgba(122,167,255,0.05)] p-4">
            <div>
              <div className="text-sm font-semibold text-[var(--fg)]">{title}</div>
              <div className="mt-2 max-w-3xl text-sm text-[var(--muted)]">{description}</div>
            </div>
            <button
              type="button"
              onClick={() => setReady(true)}
              className="inline-flex min-h-[40px] items-center justify-center rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.42)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
            >
              立即加载
            </button>
          </div>
          {placeholder}
        </div>
      )}
    </div>
  );
}

export function BehaviorSpaceDeferredVisualizations({ visualization }: { visualization: BehaviorSpaceVisualization }) {
  return (
    <div className="grid gap-4">
      <DeferredSection
        sectionId="behavior-space-showcase"
        title="2.5D 演示层按需准备"
        description="演示层只用于高价值讲解，不影响首屏的上线判断、风险成本和下一步动作，因此延后到可见或空闲时再加载。"
        renderContent={() => <LazyBehaviorSpaceSandbox visualization={visualization} />}
        placeholder={<ShowcaseSkeleton compact />}
        preloadOnIdle={false}
      />
      <DeferredSection
        sectionId="behavior-space-2d"
        title="2D 主视图正在准备"
        description="图布局、节点交互和下钻面板会在浏览器空闲或滚动接近时自动初始化，先确保价值摘要、回放和审计内容即时可读。"
        renderContent={() => <LazyBehaviorSpaceFlow visualization={visualization} />}
        placeholder={<FlowSkeleton />}
        preloadOnIdle
      />
    </div>
  );
}
