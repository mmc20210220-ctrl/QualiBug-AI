import { createContext, useContext } from 'react';
import type { SessionResult } from '../api/client';

export type AuthStatus = 'checking' | 'authenticated' | 'unauthenticated' | 'error';

export type AuthContextValue = {
  status: AuthStatus;
  session: SessionResult | null;
  error: string;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
};

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth 必须在 <AuthProvider> 内使用。');
  return ctx;
}
