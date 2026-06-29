"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

export type EvidenceDrawerTone = "critical" | "warning" | "success" | "info" | "neutral";

export interface EvidenceDrawerLink {
  label: string;
  href: string;
  kind: "page" | "artifact";
}

export interface EvidenceDrawerSection {
  title: string;
  items: readonly string[];
}

export interface EvidenceDrawerField {
  label: string;
  value: string;
}

export interface EvidenceDrawerData {
  id: string;
  typeLabel: string;
  title: string;
  summary?: string;
  reason?: string;
  remediationAction?: string;
  tone?: EvidenceDrawerTone;
  statusLabel?: string;
  fields?: readonly EvidenceDrawerField[];
  sections?: readonly EvidenceDrawerSection[];
  links?: readonly EvidenceDrawerLink[];
}

function toneClasses(tone: EvidenceDrawerTone): string {
  if (tone === "critical") return "border-[rgba(255,92,122,0.35)] bg-[rgba(255,92,122,0.12)] text-[rgba(255,226,232,0.96)]";
  if (tone === "warning") return "border-[rgba(255,196,87,0.35)] bg-[rgba(255,196,87,0.12)] text-[rgba(255,243,214,0.96)]";
  if (tone === "success") return "border-[rgba(89,243,194,0.35)] bg-[rgba(89,243,194,0.12)] text-[rgba(220,255,245,0.96)]";
  if (tone === "info") return "border-[rgba(122,167,255,0.35)] bg-[rgba(122,167,255,0.12)] text-[rgba(230,239,255,0.96)]";
  return "border-[var(--border)] bg-[rgba(255,255,255,0.05)] text-[var(--muted)]";
}

function DrawerLink({ link }: { link: EvidenceDrawerLink }) {
  const className =
    "rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] px-3 py-2 text-sm text-[var(--fg)] transition hover:border-[rgba(255,255,255,0.18)]";
  if (link.href.startsWith("/")) {
    return (
      <Link href={link.href} className={className}>
        {link.kind === "artifact" ? "打开 artifact" : "打开页面"}: {link.label}
      </Link>
    );
  }
  return (
    <a href={link.href} className={className} target="_blank" rel="noreferrer">
      {link.kind === "artifact" ? "打开 artifact" : "打开页面"}: {link.label}
    </a>
  );
}

export function EvidenceDrawer({
  open,
  data,
  onClose,
}: {
  open: boolean;
  data: EvidenceDrawerData | null;
  onClose: () => void;
}) {
  const [copyState, setCopyState] = useState<"idle" | "done" | "failed">("idle");

  const handleClose = useCallback(() => {
    setCopyState("idle");
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const original = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = original;
    };
  }, [handleClose, open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") handleClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [handleClose, open]);

  const badgeTone = useMemo(() => toneClasses(data?.tone ?? "neutral"), [data?.tone]);

  if (!open || !data) return null;

  const handleCopy = async () => {
    if (!data.remediationAction) return;
    try {
      await navigator.clipboard.writeText(data.remediationAction);
      setCopyState("done");
    } catch {
      setCopyState("failed");
    }
  };

  return (
    <div className="fixed inset-0 z-50">
      <button
        type="button"
        aria-label="关闭 Evidence Drawer"
        onClick={handleClose}
        className="absolute inset-0 bg-[rgba(5,8,14,0.72)] backdrop-blur-[2px]"
      />
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-[560px] flex-col border-l border-[var(--border)] bg-[linear-gradient(180deg,rgba(18,27,42,0.98),rgba(8,12,20,0.98))] shadow-[-28px_0_56px_rgba(0,0,0,0.35)]">
        <div className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-[0.22em] text-[var(--muted)]">{data.typeLabel}</div>
            <h2 className="mt-2 text-lg font-semibold tracking-tight text-[var(--fg)]">{data.title}</h2>
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              {data.statusLabel ? <span className={`rounded-full border px-3 py-1 ${badgeTone}`}>{data.statusLabel}</span> : null}
              <span className="rounded-full border border-[var(--border)] bg-[rgba(255,255,255,0.05)] px-3 py-1 text-[var(--muted)]">
                ID {data.id}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.05)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(255,255,255,0.18)]"
          >
            关闭
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {data.summary ? (
            <section className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] p-4">
              <div className="text-xs text-[var(--muted)]">证据摘要</div>
              <p className="mt-2 text-sm leading-6 text-[var(--fg)]">{data.summary}</p>
            </section>
          ) : null}

          {data.reason ? (
            <section className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] p-4">
              <div className="text-xs text-[var(--muted)]">原因 / 解释</div>
              <p className="mt-2 text-sm leading-6 text-[var(--muted)]">{data.reason}</p>
            </section>
          ) : null}

          {data.remediationAction ? (
            <section className="rounded-[var(--radius-md)] border border-[rgba(122,167,255,0.24)] bg-[rgba(122,167,255,0.08)] p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="text-xs text-[var(--muted)]">Remediation Action</div>
                <button
                  type="button"
                  onClick={handleCopy}
                  className="rounded-[var(--radius-sm)] border border-[rgba(122,167,255,0.24)] bg-[rgba(16,24,38,0.45)] px-3 py-2 text-sm text-[var(--fg)] hover:border-[rgba(122,167,255,0.42)]"
                >
                  复制动作
                </button>
              </div>
              <p className="mt-2 text-sm leading-6 text-[var(--fg)]">{data.remediationAction}</p>
              <div className="mt-2 text-xs text-[var(--muted)]">
                {copyState === "done" ? "已复制 remediation action。" : copyState === "failed" ? "复制失败，请手动复制。" : "可直接复制给执行或交付负责人。"}
              </div>
            </section>
          ) : null}

          {data.fields?.length ? (
            <section className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] p-4">
              <div className="text-xs text-[var(--muted)]">关键字段</div>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                {data.fields.map((field) => (
                  <div key={`${field.label}-${field.value}`} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[rgba(255,255,255,0.03)] p-3">
                    <div className="text-xs text-[var(--muted)]">{field.label}</div>
                    <div className="mt-2 text-sm text-[var(--fg)]">{field.value}</div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {data.sections?.map((section) => (
            <section key={section.title} className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] p-4">
              <div className="text-xs text-[var(--muted)]">{section.title}</div>
              <ul className="mt-3 grid gap-2 text-sm leading-6 text-[var(--muted)]">
                {section.items.length ? section.items.map((item) => <li key={`${section.title}-${item}`}>{item}</li>) : <li>—</li>}
              </ul>
            </section>
          ))}

          {data.links?.length ? (
            <section className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[rgba(16,24,38,0.45)] p-4">
              <div className="text-xs text-[var(--muted)]">关联入口</div>
              <div className="mt-3 flex flex-wrap gap-2">
                {data.links.map((link) => (
                  <DrawerLink key={`${link.kind}-${link.href}`} link={link} />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      </aside>
    </div>
  );
}
