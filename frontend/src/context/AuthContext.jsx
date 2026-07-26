import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from '../lib/api.js';

/**
 * Auth context: holds the current user + permissions and exposes login/logout.
 *
 * On mount it calls GET /auth/me; a 401 means "not logged in" and the UI
 * routes to /login. The `loading` flag covers the initial probe so we don't
 * flash the login page before we know the session state.
 */
const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const refreshMe = useCallback(async () => {
    try {
      const me = await api.me();
      setUser(me);
      return me;
    } catch (err) {
      setUser(null);
      return null;
    }
  }, []);

  useEffect(() => {
    (async () => {
      await refreshMe();
      setLoading(false);
    })();
  }, [refreshMe]);

  const login = useCallback(async (username, password) => {
    const me = await api.login(username, password);
    setUser(me);
    return me;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* ignore network errors on logout */
    }
    setUser(null);
  }, []);

  const hasPermission = useCallback(
    (key) => {
      if (!user) return false;
      return user.permissions.includes(key);
    },
    [user],
  );

  const value = { user, loading, login, logout, refreshMe, hasPermission };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>');
  return ctx;
}
