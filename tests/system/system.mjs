// System test: the whole running system checked from the outside, as a user and the browser meet it:
// the dashboard page, sign-in and access rules, starting a real simulation, the live feed (cars and
// signals over Socket.io), signal safety, stopping the run and the run history.
//
// Usage: node tests/system/system.mjs <url> <admin email> <admin password>
//   e.g. node tests/system/system.mjs http://localhost:4100 admin@itsc.local <password>   (Docker)
//        node tests/system/system.mjs http://localhost:4000 <email> <password>             (npm run dev)
// The target must run the real simulation (not USE_MOCK). The test starts its own run and stops it,
// so a playlist that was playing is interrupted. Exit code 0 = every check passed.
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../server/package.json', import.meta.url));
const { io } = require('socket.io-client');

const [base, email, password] = process.argv.slice(2);
if (!base || !email || !password) {
  console.error('Usage: node tests/system/system.mjs <url> <admin email> <admin password>');
  process.exit(2);
}
const url = base.replace(/\/$/, '');
const results = [];
let cookie = '';
let csrf = '';

// Runs one numbered check and records PASS or FAIL with the reason.
async function check(id, name, fn) {
  const t0 = Date.now();
  try {
    const note = await fn();
    results.push({ id, name, ok: true, note: note ?? '', ms: Date.now() - t0 });
    console.log(`PASS ${id} ${name}${note ? ` (${note})` : ''}`);
  } catch (err) {
    results.push({ id, name, ok: false, note: err.message, ms: Date.now() - t0 });
    console.log(`FAIL ${id} ${name}: ${err.message}`);
  }
}

const expect = (cond, msg) => {
  if (!cond) throw new Error(msg);
};

// HTTP request with the session cookie, and the CSRF token on writes unless told not to.
async function api(method, path, body, { withCsrf = true, withCookie = true } = {}) {
  const headers = { 'Content-Type': 'application/json', Origin: url };
  if (withCookie && cookie) headers.Cookie = cookie;
  if (withCsrf && csrf && method !== 'GET') headers['X-CSRF-Token'] = csrf;
  const res = await fetch(url + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await res.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    /* not JSON */
  }
  return { status: res.status, headers: res.headers, json, text };
}

// Resolves with the first socket event that passes `test`, or fails after `ms`.
function waitFor(socket, event, test, ms, what) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      socket.off(event, on);
      reject(new Error(`no ${what} within ${ms / 1000} s`));
    }, ms);
    function on(msg) {
      if (test(msg)) {
        clearTimeout(timer);
        socket.off(event, on);
        resolve(msg);
      }
    }
    socket.on(event, on);
  });
}

const GREEN = new Set(['G', 'g']);
const conflicting = (sig) => (GREEN.has(sig.N) || GREEN.has(sig.S)) && (GREEN.has(sig.E) || GREEN.has(sig.W));

console.log(`System test against ${url}\n`);

// ---- 1. the deployment is up -------------------------------------------------------------------
await check('S01', 'health check answers and the real simulation is used', async () => {
  const r = await api('GET', '/api/health');
  expect(r.status === 200 && r.json?.ok, `status ${r.status}`);
  expect(r.json.mock === false, 'gateway runs the mock simulation, not SUMO');
});

await check('S02', 'the dashboard page is served', async () => {
  const r = await api('GET', '/');
  expect(r.status === 200 && /<div id="root">/.test(r.text), `status ${r.status}`);
});

await check('S03', 'security headers are set', async () => {
  const r = await api('GET', '/');
  const csp = r.headers.get('content-security-policy') ?? '';
  expect(csp.includes("default-src 'self'"), 'no Content-Security-Policy');
  expect(r.headers.get('x-content-type-options') === 'nosniff', 'no X-Content-Type-Options');
});

// ---- 2. access control --------------------------------------------------------------------------
await check('S04', 'data is refused without signing in', async () => {
  const r = await api('GET', '/api/runs', null, { withCookie: false });
  expect(r.status === 401, `status ${r.status}`);
});

await check('S05', 'a wrong password is refused', async () => {
  const r = await api('POST', '/api/auth/login', { email, password: `${password}-wrong` });
  expect(r.status === 401, `status ${r.status}`);
});

await check('S06', 'the administrator signs in', async () => {
  const r = await api('POST', '/api/auth/login', { email, password });
  expect(r.status === 200, `status ${r.status}: ${r.json?.error ?? ''}`);
  expect(r.json.user.role === 'ADMIN', `role ${r.json.user.role}`);
  const set = r.headers.get('set-cookie') ?? '';
  expect(/HttpOnly/i.test(set), 'session cookie is not HttpOnly');
  cookie = set.split(';')[0];
  csrf = r.json.csrf;
  return r.json.user.email;
});

await check('S07', 'a change without the CSRF token is refused', async () => {
  const r = await api('POST', '/api/live/stop', {}, { withCsrf: false });
  expect(r.status === 403, `status ${r.status}`);
});

await check('S08', 'the run options offer the queue-levelling controller', async () => {
  const r = await api('GET', '/api/options');
  expect(r.status === 200, `status ${r.status}`);
  expect(JSON.stringify(r.json).includes('level_vanet'), 'level_vanet missing');
});

// ---- 3. a real simulation, end to end ---------------------------------------------------------------
const socket = io(url, { transports: ['websocket'], extraHeaders: { Cookie: cookie, Origin: url }, reconnection: false });
const states = [];
const vehicleMsgs = [];
let runId = null;
socket.on('state', (m) => runId && m.run_id === runId && states.push(m));
socket.on('vehicles', (m) => runId && m.run_id === runId && vehicleMsgs.push(m));

await check('S09', 'the live feed connects for a signed-in user', async () => {
  await waitFor(socket, 'init', () => true, 15_000, 'init message');
});

await check('S10', 'an administrator starts a simulation run', async () => {
  const r = await api('POST', '/api/live/start', { scenario: 'normal', controller: 'level_vanet', network: 'grid2x2', seed: 7 });
  expect(r.status === 202, `status ${r.status}: ${JSON.stringify(r.json)}`);
  runId = r.json.run_id;
  return runId;
});

await check('S11', 'cars appear on the live map', async () => {
  const m = await waitFor(socket, 'vehicles', (v) => v.run_id === runId && v.rows?.length > 0, 90_000, 'cars');
  await api('POST', '/api/live/speed', { speed: 4 }); // shorten the rest of the test
  return `${m.rows.length} cars at t=${m.t} s`;
});

await check('S12', 'cars move between updates', async () => {
  // Wait for several cars (one alone may be waiting at a red light), then compare 10 simulated seconds later
  const first = await waitFor(socket, 'vehicles', (v) => v.run_id === runId && v.rows?.length >= 5, 90_000, '5 cars');
  const later = await waitFor(socket, 'vehicles', (v) => v.run_id === runId && v.t >= first.t + 10, 60_000, 'a later update');
  const col = (m, name) => m.fields.indexOf(name);
  const [id, x, y] = ['id', 'x', 'y'].map((f) => col(first, f));
  const before = new Map(first.rows.map((r) => [r[id], [r[x], r[y]]]));
  const moved = later.rows.filter((r) => {
    const p = before.get(r[col(later, 'id')]);
    return p && Math.hypot(p[0] - r[col(later, 'x')], p[1] - r[col(later, 'y')]) > 1;
  }).length;
  expect(moved > 0, 'no car changed position');
  return `${moved} of ${first.rows.length} cars moved in ${later.t - first.t} s`;
});

await check('S13', 'the traffic lights change by themselves', async () => {
  const seen = new Map(); // junction -> set of phases
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    for (const s of states) for (const [tls, v] of Object.entries(s.tls ?? {})) {
      if (!seen.has(tls)) seen.set(tls, new Set());
      seen.get(tls).add(JSON.stringify(v.signal));
    }
    if ([...seen.values()].some((p) => p.size >= 3)) break;
    await new Promise((r) => setTimeout(r, 1000));
  }
  const best = Math.max(0, ...[...seen.values()].map((p) => p.size));
  expect(best >= 3, `at most ${best} different signal states seen at a junction`);
  return `${seen.size} junctions, up to ${best} signal states`;
});

await check('S14', 'safety: no junction ever shows green to crossing roads', async () => {
  // Signal updates arrive about once a second: listen until there are enough of them to judge
  const deadline = Date.now() + 90_000;
  while (states.length < 40 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 1000));
  expect(states.length >= 40, `only ${states.length} state updates received`);
  const bad = states.flatMap((s) => Object.entries(s.tls ?? {}).filter(([, v]) => v.signal && conflicting(v.signal)).map(([tls]) => `${tls}@${s.t}`));
  expect(bad.length === 0, `conflicting greens: ${bad.slice(0, 5).join(', ')}`);
  return `${states.length} updates checked`;
});

await check('S15', 'the live status reports the running run', async () => {
  const r = await api('GET', '/api/live/status');
  expect(r.status === 200 && r.json.running, `running=${r.json?.running}`);
  const controller = r.json.entry?.entry?.controller;
  expect(r.json.entry?.run_id === runId, `running ${r.json.entry?.run_id}, expected ${runId}`);
  expect(controller === 'level_vanet', `controller ${controller}`);
});

await check('S16', 'the administrator stops the run', async () => {
  const r = await api('POST', '/api/live/stop', {});
  expect(r.status === 200 && r.json.stopped === true, `status ${r.status}: ${JSON.stringify(r.json)}`);
});

await check('S17', 'the run is kept in the history', async () => {
  const r = await api('GET', `/api/runs/${encodeURIComponent(runId)}`);
  expect(r.status === 200, `status ${r.status}`);
  return `status ${r.json.status ?? r.json.run?.status ?? '?'}`;
});

socket.close();

// ---- 4. sign out ------------------------------------------------------------------------------------
await check('S18', 'after signing out the session no longer works', async () => {
  const out = await api('POST', '/api/auth/logout', {});
  expect(out.status === 200 || out.status === 204, `logout status ${out.status}`);
  const me = await api('GET', '/api/runs');
  expect(me.status === 401, `status ${me.status}`);
});

// ---- summary --------------------------------------------------------------------------------------------
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} system checks passed`);
if (process.env.SYSTEM_REPORT) {
  const fs = await import('node:fs');
  fs.writeFileSync(process.env.SYSTEM_REPORT, JSON.stringify({ url, at: new Date().toISOString(), results }, null, 2));
}
process.exit(failed.length ? 1 : 0);
