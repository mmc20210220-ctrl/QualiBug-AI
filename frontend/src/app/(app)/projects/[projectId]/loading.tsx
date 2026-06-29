function SkeletonBlock({ className }: { className: string }) {
  return <div className={`animate-pulse rounded-[var(--radius-sm)] bg-[rgba(255,255,255,0.06)] ${className}`} />;
}

export default function ProjectWorkspaceLoading() {
  return (
    <div className="grid gap-4">
      <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)] bg-[linear-gradient(180deg,rgba(16,24,38,0.82),rgba(10,16,27,0.92))] p-6 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
          <div className="grid gap-3">
            <SkeletonBlock className="h-3 w-28" />
            <SkeletonBlock className="h-8 w-64" />
            <SkeletonBlock className="h-4 w-full max-w-3xl" />
            <SkeletonBlock className="h-4 w-full max-w-2xl" />
            <div className="mt-2 flex flex-wrap gap-2">
              <SkeletonBlock className="h-10 w-40" />
              <SkeletonBlock className="h-10 w-36" />
              <SkeletonBlock className="h-10 w-36" />
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-1">
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={`hero-skeleton:${index}`} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-4">
                <SkeletonBlock className="h-3 w-20" />
                <SkeletonBlock className="mt-3 h-5 w-36" />
                <SkeletonBlock className="mt-3 h-4 w-full" />
                <SkeletonBlock className="mt-2 h-4 w-5/6" />
              </div>
            ))}
          </div>
        </div>

        <div className="mt-6 grid gap-3 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <div key={`journey-skeleton:${index}`} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(8,13,21,0.48)] p-4">
              <SkeletonBlock className="h-3 w-10" />
              <SkeletonBlock className="mt-3 h-5 w-40" />
              <SkeletonBlock className="mt-3 h-4 w-full" />
              <SkeletonBlock className="mt-2 h-4 w-4/5" />
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.55)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid gap-3">
            <SkeletonBlock className="h-3 w-20" />
            <SkeletonBlock className="h-6 w-72" />
            <SkeletonBlock className="h-4 w-full max-w-2xl" />
          </div>
          <SkeletonBlock className="h-10 w-36" />
        </div>

        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <div key={`quick-entry-skeleton:${index}`} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.4)] p-4">
              <div className="flex items-center justify-between gap-3">
                <SkeletonBlock className="h-5 w-28" />
                <SkeletonBlock className="h-6 w-16 rounded-full" />
              </div>
              <SkeletonBlock className="mt-3 h-4 w-full" />
              <SkeletonBlock className="mt-2 h-4 w-5/6" />
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.42)] p-6 shadow-[var(--shadow-1)] backdrop-blur">
        <div className="grid gap-3">
          <SkeletonBlock className="h-3 w-24" />
          <SkeletonBlock className="h-7 w-64" />
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={`panel-skeleton:${index}`} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-4">
                <SkeletonBlock className="h-3 w-20" />
                <SkeletonBlock className="mt-3 h-5 w-28" />
                <SkeletonBlock className="mt-3 h-4 w-full" />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
