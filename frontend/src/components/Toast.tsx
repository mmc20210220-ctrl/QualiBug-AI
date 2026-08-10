import { useState, useCallback, type ReactNode } from 'react';
import { ToastContext } from './useToast';

interface ToastItem {
  id: number;
  message: string;
  tone: 'success' | 'danger' | 'warning' | 'info';
  hasCode: boolean;
}

let _nextId = 0;

const QB_CODE_RE = /QB-[A-Z]\d{3}/;

const LEGACY_MESSAGE_REPLACEMENTS: ReadonlyArray<readonly [string, string]> = [
  ['璇峰厛绮樿创涓€涓湪绾胯祫鏂欏叆鍙ｆ湇鍔″櫒 URL', '请先粘贴一个在线资料入口 URL'],
  ['璇疯緭鍏ユ湁鏁堢殑 HTTP(S) URL', '请输入有效的 HTTP(S) URL'],
  ['鍦ㄧ嚎璧勬枡鍏ュ彛蹇呴』浣跨敤 HTTP(S) URL', '在线资料入口必须使用 HTTP(S) URL'],
  ['鎺ュ叆鍣ㄧ殑 Manifest 鏈０鏄庡彲鐢ㄧ殑 URL 鑼冨洿瀛楁', '连接器 Manifest 未声明可用的 URL 范围字段'],
  ['褰撳墠娌℃湁 Manifest 澹版槑 URL 鍏ュ彛鐨勮繛鎺ュ櫒', '当前没有声明 URL 入口的连接器 Manifest'],
];

export function normalizeToastMessage(message: string): string {
  return LEGACY_MESSAGE_REPLACEMENTS.reduce(
    (normalized, [legacy, readable]) => normalized.replaceAll(legacy, readable),
    message,
  );
}

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
