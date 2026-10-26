// The signed-in user. The session itself is an HttpOnly cookie; this context only mirrors what the
// gateway says (/api/auth/me) and keeps the CSRF token for the API client. Role checks here decide
// what to show; the gateway enforces them.
import { createContext, createElement, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { clearPersistentState } from './usePersistentState.js';
import { api, setCsrfToken, setSessionLostHandler } from '../api.js';

const AuthContext = createContext(null);

export const ROLE_LABEL = { ADMIN: 'Administrator', RESEARCHER: 'Researcher', VIEWER: 'Viewer' };

export function AuthProvider({ children }) {
  const [state, setState] = useState({ status: 'loading', user: null, registration: false, error: null });

  const apply = useCallback((res) => {
    setCsrfToken(res?.user ? res.csrf : null);
    setState((s) => ({ status: 'ready', user: res?.user ?? null, registration: res?.registration ?? s.registration, error: null }));
  }, []);

  const refresh = useCallback(async () => {
    try {
      apply(await api.me());
    } catch (err) {
      // The gateway cannot be reached: keep what we know and say so (never pretend to be signed out)
      setState((s) => ({ ...s, status: s.status === 'loading' ? 'unreachable' : s.status, error: err.message }));
    }
  }, [apply]);

  useEffect(() => {
    refresh();
    setSessionLostHandler(() => apply({ user: null }));
    return () => setSessionLostHandler(null);
  }, [refresh, apply]);

  const login = useCallback(async (email, password) => apply(await api.login(email, password)), [apply]);
  const register = useCallback(async (email, name, password) => apply(await api.register(email, name, password)), [apply]);
  const logout = useCallback(async () => {
    clearPersistentState();
    try {
      await api.logout();
    } finally {
      apply({ user: null });
    }
  }, [apply]);
  const setUser = useCallback((user) => setState((s) => ({ ...s, user })), []);

  const value = useMemo(() => {
    const role = state.user?.role ?? null;
    return {
      ...state, login, register, logout, refresh, setUser,
      isAdmin: role === 'ADMIN',
      canControl: role === 'ADMIN' || role === 'RESEARCHER',
    };
  }, [state, login, register, logout, refresh, setUser]);
  return createElement(AuthContext.Provider, { value }, children);
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
