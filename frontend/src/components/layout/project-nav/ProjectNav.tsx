"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { getActiveProjectNavKey, projectNavItems } from "@/components/layout/navigation";

export function ProjectNav({ projectId }: { projectId: string }) {
  const pathname = usePathname();
  const activeKey = getActiveProjectNavKey(pathname, projectId);

  return (
    <nav className="grid gap-1">
      {projectNavItems.map((item) => {
        const href = item.href(projectId);
        const active = activeKey === item.key;

        return (
          <Link
            key={item.key}
            href={href}
            aria-current={active ? "page" : undefined}
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
