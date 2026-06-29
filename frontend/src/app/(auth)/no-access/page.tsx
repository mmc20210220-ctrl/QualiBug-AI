import Link from "next/link";

export default function NoAccessPage() {
  return (
    <div className="w-full max-w-[720px] rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.75)] p-7 shadow-[var(--shadow-1)] backdrop-blur">
      <div className="text-xs text-[var(--muted)]">权限</div>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">暂无访问权限</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">
        当前账号没有该租户/项目权限。请联系管理员为你授予对应项目访问，或通过企业权限系统提交申请（占位）。
      </p>

      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        <Link
          className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(14,22,34,0.55)] px-4 py-3 text-center text-sm text-[var(--muted)] hover:text-[var(--fg)]"
          href="/projects"
        >
          返回项目列表
        </Link>
        <Link
          className="rounded-[var(--radius-sm)] bg-[linear-gradient(135deg,rgba(89,243,194,0.16),rgba(122,167,255,0.10))] px-4 py-3 text-center text-sm font-medium ring-1 ring-[rgba(255,255,255,0.10)] hover:ring-[rgba(255,255,255,0.18)]"
          href="/auth/logout"
        >
          切换账号
        </Link>
      </div>
    </div>
  );
}

