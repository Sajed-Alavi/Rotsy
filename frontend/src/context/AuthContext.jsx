import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api, ApiError } from '../lib/api.js';

/**
 * Auth context: holds the current user + permissions and exposes login/logout.
 *
 * On mount it calls GET /auth/me; a confirmed 401 means "not logged in" and
 * the UI routes to /login. The `loading` flag covers the initial probe so we
 * don't flash the login page before we know the session state.
 */
const AuthContext = createContext(null);

// A backend redeploy (container recreate: wait-for-db, migrate, seed, start
// uvicorn — see backend/entrypoint.sh) makes the API briefly unreachable.
// That must not look like a logout: only a confirmed 401 from /auth/me means
// the session cookie is actually invalid. Any other failure (network error,
// a 502/503 while the backend is still starting) gets a few short retries
// before giving up, so an in-flight tab rides out a normal
// `docker compose up --build` instead of losing an otherwise-valid session.
const TRANSIENT_RETRY_DELAYS_MS = [500, 1500, 3000];

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const refreshMe = useCallback(async () => {
    for (let attempt = 0; ; attempt++) {
      try {
        const me = await api.me();
        setUser(me);
        return me;
      } catch (err) {
        const unauthenticated = err instanceof ApiError && err.status === 401;
        if (unauthenticated) {
          setUser(null);
          return null;
        }
        if (attempt >= TRANSIENT_RETRY_DELAYS_MS.length) {
          // Retries exhausted on a non-401 failure — the backend may still
          // be down, but we don't know the session is actually invalid, so
          // leave `user` as it was rather than forcing a real login.
          return null;
        }
        await new Promise((resolve) => setTimeout(resolve, TRANSIENT_RETRY_DELAYS_MS[attempt]));
      }
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
