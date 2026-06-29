export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-dvh bg-[radial-gradient(1200px_600px_at_30%_-20%,rgba(89,243,194,0.16),transparent_60%),radial-gradient(900px_500px_at_80%_0%,rgba(122,167,255,0.18),transparent_55%),linear-gradient(180deg,var(--bg),#070a0f)]">
      <div className="mx-auto flex min-h-dvh w-full max-w-[1100px] items-center px-6 py-10">
        {children}
      </div>
    </div>
  );
}

