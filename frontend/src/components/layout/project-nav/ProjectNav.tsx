"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const items = [
  { href: (projectId: string) => `/projects/${projectId}`, label: "项目工作区" },
  { href: (projectId: string) => `/projects/${projectId}/behavior-space`, label: "Behavior Space" },
  { href: (projectId: string) => `/projects/${projectId}/capabilities`, label: "能力中心" },
  { href: (projectId: string) => `/projects/${projectId}/risks`, label: "风险证据" },
  { href: (projectId: string) => `/projects/${projectId}/execution`, label: "执行" },
  { href: (projectId: string) => `/projects/${projectId}/reports/executive`, label: "报告" },
  { href: (projectId: string) => `/projects/${projectId}/roi`, label: "ROI/价值" },
] as const;

export function ProjectNav({ projectId }: { projectId: string }) {
  const pathname = usePathname();

  return (
    <nav className="grid gap-1">
      {items.map((item) => {
        const href = item.href(encodeURIComponent(projectId));
        const active = pathname === href || pathname.startsWith(`${href}/`);

        return (
          <Link
            key={item.label}
            href={href}
            className={[
              "rounded-[var(--radius-sm)] px-3 py-2 text-sm transition",
              active
                ? "bg-[rgba(89,243,194,0.12)] text-[var(--fg)] ring-1 ring-[rgba(89,243,194,0.22)]"
                : "text-[var(--muted)] hover:bg-[rgba(255,255,255,0.06)] hover:text-[var(--fg)]",
            ].join(" ")}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
