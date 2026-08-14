import { useState, useCallback, type ReactNode } from 'react';
import { ToastContext } from './useToast';
import { normalizeToastMessage } from '../lib/toast-message';

interface ToastItem {
  id: number;
  message: string;
  tone: 'success' | 'danger' | 'warning' | 'info';
  hasCode: boolean;
}

let _nextId = 0;

const QB_CODE_RE = /QB-[A-Z]\d{3}/;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const show = useCallback((message: string, tone: ToastItem['tone'] = 'info') => {
    const normalizedMessage = normalizeToastMessage(message);
    const id = ++_nextId;
    const hasCode = QB_CODE_RE.test(normalizedMessage);
    setToasts(prev => [...prev, { id, message: normalizedMessage, tone, hasCode }]);
    // Errors with product codes stay longer so users can read the hint
    const duration = hasCode ? 10000 : 4000;
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), duration);
  }, []);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="true">
        {toasts.map(t => {
          return (
            <div key={t.id} className={`toast-item tone-${t.tone}${t.hasCode ? ' toast-has-code' : ''}`}>
              <span className="toast-item-icon" aria-hidden="true">
                {t.tone === 'success' ? '✓' : t.tone === 'danger' ? '✗' : t.tone === 'warning' ? '⚠' : 'ℹ'}
              </span>
              <span className="toast-item-copy">{t.message.split('\n').map((line, i) => (
                <span key={i}>{i > 0 && <br />}{line}</span>
              ))}</span>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
