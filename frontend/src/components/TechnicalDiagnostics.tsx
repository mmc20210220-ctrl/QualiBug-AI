import { useState } from 'react';
import type { ReactNode } from 'react';

interface TechnicalDiagnosticsProps {
  children: ReactNode;
  defaultOpen?: boolean;
}

export function TechnicalDiagnostics({ children, defaultOpen = false }: TechnicalDiagnosticsProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <section className="tech-diagnostics mb-4">
      <button
        type="button"
        className="tech-diagnostics-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="tech-diagnostics-icon">{open ? '▾' : '▸'}</span>
        <span className="tech-diagnostics-label">技术诊断详情</span>
        <span className="tech-diagnostics-hint">检测流程、链路与治理状态（内部运营参考）</span>
      </button>
      {open && <div className="tech-diagnostics-body">{children}</div>}
    </section>
  );
}

export default TechnicalDiagnostics;
