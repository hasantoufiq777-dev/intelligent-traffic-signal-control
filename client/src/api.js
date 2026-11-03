// REST client for the gateway. Base URL is empty (same origin / Vite proxy) unless VITE_API_URL is set.
export const API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request(path, { method = 'GET', body, token } = {}) {
  const headers = {};
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (token) headers['x-admin-token'] = token;
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError('Cannot reach the gateway', 0, null);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) throw new ApiError(data?.error || `Request failed (${res.status})`, res.status, data);
  return data;
}

export const api = {
  health: () => request('/api/health'),
  options: () => request('/api/options'),
  liveStatus: () => request('/api/live/status'),
  playlist: () => request('/api/live/playlist'),
  summary: () => request('/api/experiments/summary'),
  runs: (limit = 20) => request(`/api/runs?limit=${limit}`),
  run: (id) => request(`/api/runs/${encodeURIComponent(id)}`),
  start: (token, entry) => request('/api/live/start', { method: 'POST', body: entry, token }),
  stop: (token) => request('/api/live/stop', { method: 'POST', token }),
  sumoGui: () => request('/api/sumo/gui'),
  setSumoGui: (enabled, token) => request('/api/sumo/gui', { method: 'POST', body: { enabled }, token }),
};
