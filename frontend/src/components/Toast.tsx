import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';

interface ToastItem {
  id: number;
  message: string;
  tone: 'success' | 'danger' | 'warning' | 'info';
}

interface ToastCtx {
  show: (message: string, tone?: ToastItem['tone']) => void;
}

const ToastContext = createContext<ToastCtx>({ show: () => {} });

let _nextId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const show = useCallback((message: string, tone: ToastItem['tone'] = 'info') => {
    const id = ++_nextId;
    setToasts(prev => [...prev, { id, message, tone }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 4000);
  }, []);

  const colors: Record<string, { bg: string; border: string; color: string }> = {
    success: { bg: 'var(--success-muted)', border: '#bbf7d0', color: 'var(--success)' },
    danger: { bg: 'var(--danger-muted)', border: '#fecaca', color: 'var(--danger)' },
    warning: { bg: 'var(--warning-muted)', border: '#fde68a', color: 'var(--warning)' },
    info: { bg: 'var(--primary-muted)', border: '#c7d2fe', color: 'var(--primary)' },
  };

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      {/* Toast container */}
      <div style={{
        position: 'fixed', top: 68, right: 24, zIndex: 1000,
        display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 380,
      }}>
        {toasts.map(t => {
          const c = colors[t.tone];
          return (
            <div key={t.id} style={{
              background: c.bg, border: `1px solid ${c.border}`, color: c.color,
              padding: '12px 16px', borderRadius: 'var(--radius-sm)', fontSize: 13, fontWeight: 500,
              boxShadow: '0 4px 12px rgba(0,0,0,.08)', backdropFilter: 'blur(8px)',
              animation: 'toastIn 0.3s ease',
            }}>
              {t.tone === 'success' ? '✓ ' : t.tone === 'danger' ? '✗ ' : t.tone === 'warning' ? '⚠ ' : 'ℹ '}
              {t.message}
            </div>
          );
        })}
      </div>
      <style>{`@keyframes toastIn { from { opacity: 0; transform: translateX(20px) } to { opacity: 1; transform: translateX(0) } }`}</style>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
