/**
 * API client.
 *
 * Auth is cookie-based: every request sends `credentials: 'include'` so the
 * httpOnly access/refresh cookies ride along automatically. On 401 we try one
 * transparent refresh; if that fails, the caller surfaces a redirect to login.
 */

const BASE = import.meta.env.VITE_API_BASE_URL || '/api';

export class ApiError extends Error {
  constructor(message, { status, payload } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

async function rawRequest(path, { method = 'GET', body, signal } = {}) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      credentials: 'include',
      signal,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(`Network error: ${err.message}`);
  }

  const text = await response.text();
  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    const message =
      (payload && (payload.detail || payload.message)) ||
      `Request failed (${response.status})`;
    throw new ApiError(message, { status: response.status, payload });
  }
  return payload;
}

function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** Attempt a token refresh once; returns true on success. */
export async function tryRefresh() {
  try {
    await rawRequest('/auth/refresh', { method: 'POST' });
    return true;
  } catch {
    return false;
  }
}

/**
 * Request wrapper that retries once on 401 via /auth/refresh.
 * Throws ApiError (status 401) if refresh also fails — callers (e.g. the auth
 * context) interpret that as "logged out".
 */
export async function request(path, opts) {
  try {
    return await rawRequest(path, opts);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401 && !(opts && opts._retried)) {
      const refreshed = await tryRefresh();
      if (refreshed) return rawRequest(path, { ...opts, _retried: true });
    }
    throw err;
  }
}

export const api = {
  get: (path, signal) => request(path, { method: 'GET', signal }),
  post: (path, body) => request(path, { method: 'POST', body }),
  put: (path, body) => request(path, { method: 'PUT', body }),
  patch: (path, body) => request(path, { method: 'PATCH', body }),
  delete: (path) => request(path, { method: 'DELETE' }),
  login: (username, password) => rawRequest('/auth/login', { method: 'POST', body: { username, password } }),
  logout: () => rawRequest('/auth/logout', { method: 'POST' }),
  me: () => request('/auth/me'),
};
