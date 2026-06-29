"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { getActiveGlobalNavKey, globalNavItems } from "@/components/layout/navigation";

export function GlobalNav() {
  const pathname = usePathname();
  const activeKey = getActiveGlobalNavKey(pathname);

  return (
    <nav className="grid gap-1">
      {globalNavItems.map((item) => {
        const active = activeKey === item.key;

        return (
          <Link
            key={item.key}
            href={item.href}
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
