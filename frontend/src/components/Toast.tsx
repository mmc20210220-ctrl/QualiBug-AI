import { useState, useCallback, type ReactNode } from 'react';
import { ToastContext } from './useToast';

interface ToastItem {
  id: number;
  message: string;
  tone: 'success' | 'danger' | 'warning' | 'info';
}

let _nextId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const show = useCallback((message: string, tone: ToastItem['tone'] = 'info') => {
    const id = ++_nextId;
    setToasts(prev => [...prev, { id, message, tone }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 4000);
  }, []);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="true">
        {toasts.map(t => {
          return (
            <div key={t.id} className={`toast-item tone-${t.tone}`}>
              <span className="toast-item-icon" aria-hidden="true">
                {t.tone === 'success' ? '✓' : t.tone === 'danger' ? '✗' : t.tone === 'warning' ? '⚠' : 'ℹ'}
              </span>
              <span className="toast-item-copy">{t.message}</span>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
