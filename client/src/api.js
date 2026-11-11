// REST client for the gateway. Base URL is empty (same origin / Vite proxy) unless VITE_API_URL is set.
// The session lives in an HttpOnly cookie the browser sends by itself; state-changing requests also
// carry the session's CSRF token, which the gateway hands out at sign-in and from /api/auth/me.
export const API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
    this.code = body?.code ?? null;
  }
}

let csrfToken = null;
let onSessionLost = null;

export function setCsrfToken(token) {
  csrfToken = token || null;
}

/** Called when the gateway answers "not signed in" to a request that expected a session. */
export function setSessionLostHandler(fn) {
  onSessionLost = fn;
}

async function request(path, { method = 'GET', body, quiet = false } = {}) {
  const headers = {};
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (method !== 'GET' && csrfToken) headers['x-csrf-token'] = csrfToken;
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method, headers, credentials: API_BASE ? 'include' : 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError('Cannot reach the server. Check your connection and try again.', 0, null);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    if (res.status === 401 && data?.code === 'unauthenticated' && !quiet) onSessionLost?.();
    throw new ApiError(data?.error || `Request failed (${res.status})`, res.status, data);
  }
  return data;
}

const post = (path, body) => request(path, { method: 'POST', body });
const q = (params) => {
  const s = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString();
  return s ? `?${s}` : '';
};

export const api = {
  // session
  me: () => request('/api/auth/me', { quiet: true }),
  login: (email, password) => post('/api/auth/login', { email, password }),
  logout: () => post('/api/auth/logout'),
  register: (email, name, password) => post('/api/auth/register', { email, name, password }),
  forgotPassword: (email) => post('/api/auth/forgot', { email }),
  resetPassword: (token, password) => post('/api/auth/reset', { token, password }),
  updateProfile: (name) => request('/api/auth/profile', { method: 'PATCH', body: { name } }),
  changePassword: (current_password, new_password) => post('/api/auth/password', { current_password, new_password }),

  // live simulation and results
  health: () => request('/api/health'),
  options: () => request('/api/options'),
  liveStatus: () => request('/api/live/status'),
  playlist: () => request('/api/live/playlist'),
  summary: () => request('/api/experiments/summary'),
  runs: (limit = 20, offset = 0) => request(`/api/runs${q({ limit, offset })}`),
  run: (id) => request(`/api/runs/${encodeURIComponent(id)}`),
  start: (entry) => post('/api/live/start', entry),
  stop: () => post('/api/live/stop'),
  sumoGui: () => request('/api/sumo/gui'),
  setSumoGui: (enabled) => post('/api/sumo/gui', { enabled }),
  driver: () => request('/api/driver'),
  setDriver: (enabled) => post('/api/driver', { enabled }),
  releaseDriver: () => post('/api/driver/release'),

  // administration
  users: (limit = 50, offset = 0) => request(`/api/users${q({ limit, offset })}`),
  createUser: (user) => post('/api/users', user),
  updateUser: (id, change) => request(`/api/users/${id}`, { method: 'PATCH', body: change }),
  deleteUser: (id) => request(`/api/users/${id}`, { method: 'DELETE' }),
  resetLink: (id) => post(`/api/users/${id}/reset-link`),
  sessions: (limit = 50, offset = 0) => request(`/api/sessions${q({ limit, offset })}`),
  revokeSession: (id) => request(`/api/sessions/${id}`, { method: 'DELETE' }),
  audit: (limit = 50, offset = 0, action = '') => request(`/api/audit${q({ limit, offset, action })}`),
  settings: () => request('/api/settings'),
  saveSettings: (settings) => request('/api/settings', { method: 'PUT', body: settings }),
  system: () => request('/api/system'),
};
