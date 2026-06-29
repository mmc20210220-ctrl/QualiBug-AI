import Link from "next/link";

export function GlobalNav() {
  return (
    <nav className="grid gap-1">
      <Link
        href="/projects"
        className="rounded-[var(--radius-sm)] px-3 py-2 text-sm text-[var(--fg)] hover:bg-[rgba(255,255,255,0.06)]"
      >
        项目列表
      </Link>
      <Link
        href="/login"
        className="rounded-[var(--radius-sm)] px-3 py-2 text-sm text-[var(--muted)] hover:bg-[rgba(255,255,255,0.06)] hover:text-[var(--fg)]"
      >
        登录
      </Link>
    </nav>
  );
}

