"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useTransition } from "react";

function setSearchParams(current: URLSearchParams, patch: Record<string, string | null>) {
  const next = new URLSearchParams(current.toString());
  for (const [k, v] of Object.entries(patch)) {
    if (!v) next.delete(k);
    else next.set(k, v);
  }
  return next;
}

export function RiskFilters() {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [pending, startTransition] = useTransition();

  const values = useMemo(() => {
    const severity = searchParams.get("severity") ?? "";
    const status = searchParams.get("status") ?? "";
    const launchBlocking = searchParams.get("launch_blocking") ?? "";
    return { severity, status, launchBlocking };
  }, [searchParams]);

  const push = (patch: Record<string, string | null>) => {
    const next = setSearchParams(searchParams, patch);
    const qs = next.toString();
    startTransition(() => {
      router.push(qs ? `${pathname}?${qs}` : pathname);
    });
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      <label className="grid gap-1 text-xs text-[var(--muted)]">
        严重度
        <select
          value={values.severity}
          onChange={(e) => push({ severity: e.target.value || null })}
          disabled={pending}
          className="h-9 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] px-3 text-sm text-[var(--fg)]"
        >
          <option value="">全部</option>
          <option value="critical">critical</option>
          <option value="high">high</option>
          <option value="medium">medium</option>
          <option value="low">low</option>
          <option value="info">info</option>
        </select>
      </label>

      <label className="grid gap-1 text-xs text-[var(--muted)]">
        状态
        <select
          value={values.status}
          onChange={(e) => push({ status: e.target.value || null })}
          disabled={pending}
          className="h-9 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] px-3 text-sm text-[var(--fg)]"
        >
          <option value="">全部</option>
          <option value="confirmed">confirmed</option>
          <option value="investigating">investigating</option>
          <option value="resolved">resolved</option>
        </select>
      </label>

      <label className="grid gap-1 text-xs text-[var(--muted)]">
        上线阻断
        <select
          value={values.launchBlocking}
          onChange={(e) => push({ launch_blocking: e.target.value || null })}
          disabled={pending}
          className="h-9 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] px-3 text-sm text-[var(--fg)]"
        >
          <option value="">全部</option>
          <option value="true">是</option>
          <option value="false">否</option>
        </select>
      </label>

      <button
        type="button"
        disabled={pending}
        onClick={() => push({ severity: null, status: null, launch_blocking: null })}
        className="mt-5 h-9 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.25)] px-3 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
      >
        清空
      </button>
    </div>
  );
}

