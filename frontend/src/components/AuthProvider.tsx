import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { getProjects, getSession, logout as logoutApi, type SessionResult } from '../api/client';
import { AuthContext, type AuthContextValue, type AuthStatus } from './useAuth';

/**
 * 认证权威状态机。生产环境下「服务端 Cookie 会话」是唯一认证权威：
 * - sessionStorage 标记（qualibug_validated_session）只是已校验会话的快路径，
 *   绝不能让「sessionStorage 为空」被误判为「未登录」——新标签页 / 刷新后
 *   sessionStorage 天然为空，但 Cookie 仍有效，此时必须向后端请求会话校验。
 * - 网络失败 / 5xx 属于 error，不是 unauthenticated，禁止把它降级为未登录。
 *
 * 状态：checking | authenticated | unauthenticated | error。
 */
const AUTH_CHANGE_EVENT = 'qualibug-auth-change';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('checking');
  const [session, setSession] = useState<SessionResult | null>(null);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    try {
      const result = await getSession({ force: true });
      if (result) {
        // 会话已经确认后立即预热项目目录。业务页面随后调用 resolveProjectId() 时
        // 会复用同一个 projectsCache Promise，避免「页面挂载后才开始第二段网络等待」。
        // 预热失败不影响认证状态，业务页面仍会按原错误语义自行处理。
        void getProjects().catch(() => undefined);
        setSession(result);
        setStatus('authenticated');
      } else {
        setSession(null);
        setStatus('unauthenticated');
      }
      setError('');
    } catch (caught: unknown) {
      setSession(null);
      setError(caught instanceof Error ? caught.message : '无法连接服务');
      setStatus('error');
    }
  }, []);

  // 首次挂载即向后端校验会话（Spec §53：checking → 请求后端会话）。
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 登录 / 登出 / 跨标签页会话变化统一通过 qualibug-auth-change 事件同步。
  useEffect(() => {
    const onAuthChange = () => {
      void refresh();
    };
    window.addEventListener(AUTH_CHANGE_EVENT, onAuthChange);
    return () => window.removeEventListener(AUTH_CHANGE_EVENT, onAuthChange);
  }, [refresh]);

  const signOut = useCallback(async () => {
    await logoutApi();
    setSession(null);
    setStatus('unauthenticated');
    setError('');
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, session, error, refresh, signOut }),
    [status, session, error, refresh, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
