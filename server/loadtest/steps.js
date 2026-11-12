// Stepped load test for authenticated users: for each step N it opens N live connections with a
// signed-in session, holds them while calling the API at a steady rate, and records connection
// success, live-stream delivery latency, API latency percentiles and the gateway's CPU, memory and
// event-loop lag. Optional extras: a spike (all connections at once), mass reconnection, a burst
// of sign-ins, and a soak (hold the last step for many minutes).
//
//   node loadtest/steps.js --url http://127.0.0.1:4100 --email admin@example.org [--password ...]
//        [--steps 10,50,100,250,500,750,1000] [--hold 20] [--rps 40] [--spike 1000] [--reconnect 500]
//        [--logins 50] [--soak 30] [--out result.json]
//
// The password comes from --password or LT_PASSWORD. Run the gateway with AUTH_RATE_LIMIT and
// RATE_LIMIT_PER_MIN raised (all test traffic comes from one address) and SESSIONS_PER_USER above --logins.
import fs from 'node:fs';
import os from 'node:os';
import { io as ioc } from 'socket.io-client';

const arg = (name, def) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : process.env[`LT_${name.toUpperCase()}`] ?? def;
};
const URL = arg('url', 'http://127.0.0.1:4100');
const EMAIL = arg('email', '');
const PASSWORD = arg('password', '');
const STEPS = arg('steps', '10,50,100,250,500,750,1000').split(',').map(Number).filter((n) => n > 0);
const HOLD_S = Number(arg('hold', '20'));
const RPS = Number(arg('rps', '40'));
const SPIKE = Number(arg('spike', '0'));
const RECONNECT = Number(arg('reconnect', '0'));
const LOGINS = Number(arg('logins', '0'));
const SOAK_MIN = Number(arg('soak', '0'));
const OUT = arg('out', '');
const API_PATHS = ['/api/live/status', '/api/runs?limit=20', '/api/auth/me', '/api/options'];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pct = (arr, p) => {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  return +s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))].toFixed(1);
};
const stats = (arr) => ({ n: arr.length, p50: pct(arr, 50), p95: pct(arr, 95), p99: pct(arr, 99), max: pct(arr, 100) });

let cookie = '';
let csrf = '';
const api = async (path, opts = {}) => {
  const t0 = performance.now();
  const res = await fetch(`${URL}${path}`, { ...opts, headers: { ...(opts.headers ?? {}), cookie } });
  const body = await res.json().catch(() => null);
  return { status: res.status, body, ms: performance.now() - t0 };
};

async function signIn() {
  const res = await fetch(`${URL}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }) });
  if (!res.ok) throw new Error(`Sign-in failed (${res.status})`);
  cookie = res.headers.getSetCookie()[0].split(';')[0];
  csrf = (await res.json()).csrf;
}

/** Open n connections over rampMs; resolve when each has connected or failed. */
async function openMany(n, rampMs, sink) {
  const sockets = [];
  const outcome = { connected: 0, refused: 0, failed: 0, connect_ms: [] };
  const done = [];
  for (let i = 0; i < n; i += 1) {
    const t0 = performance.now();
    const s = ioc(URL, { transports: ['websocket'], forceNew: true, reconnection: false, timeout: 30_000, extraHeaders: { Cookie: cookie } });
    sockets.push(s);
    done.push(new Promise((resolve) => {
      s.once('connect', () => {
        outcome.connected += 1;
        outcome.connect_ms.push(performance.now() - t0);
        resolve();
      });
      s.once('connect_error', (err) => {
        if (err?.data?.code === 'capacity') outcome.refused += 1;
        else outcome.failed += 1;
        resolve();
      });
    }));
    s.on('state', (m) => {
      if (sink.on && m.sent_at) sink.latency.push(Date.now() - m.sent_at);
      if (sink.on) sink.states += 1;
    });
    s.on('disconnect', () => { if (sink.on) sink.drops += 1; });
    if (rampMs > 0 && i % 25 === 24) await sleep((rampMs / n) * 25);
  }
  await Promise.all(done);
  return { sockets, outcome };
}

async function hold(seconds, sink) {
  sink.on = true;
  const apiMs = [];
  let apiErrors = 0;
  const m0 = (await api('/api/metrics')).body;
  const t0 = Date.now();
  let k = 0;
  const pending = [];
  while (Date.now() - t0 < seconds * 1000) {
    const p = api(API_PATHS[k % API_PATHS.length]).then((r) => {
      if (r.status === 200) apiMs.push(r.ms);
      else apiErrors += 1;
    }).catch(() => { apiErrors += 1; });
    pending.push(p);
    k += 1;
    await sleep(1000 / RPS);
  }
  await Promise.all(pending);
  const dt = (Date.now() - t0) / 1000;
  const m1 = (await api('/api/metrics')).body;
  sink.on = false;
  return {
    seconds: +dt.toFixed(1),
    api: { ...stats(apiMs), errors: apiErrors, rps: +(apiMs.length / dt).toFixed(1) },
    server: {
      viewers: m1.viewers,
      cpu_pct_one_core: +(((m1.cpu.user + m1.cpu.system - m0.cpu.user - m0.cpu.system) / 1e6 / dt) * 100).toFixed(1),
      rss_mb: m1.memory_mb.rss, heap_mb: m1.memory_mb.heapUsed,
      event_loop_lag_ms: m1.event_loop_ms,
      out_kb_s: +((m1.live.bytes_broadcast - m0.live.bytes_broadcast) / 1024 / dt).toFixed(0),
      persist_errors: m1.live.persist_errors, bad_lines: m1.live.bad_lines,
    },
  };
}

const closeAll = async (sockets) => {
  sockets.forEach((s) => s.close());
  await sleep(1500);
};

async function main() {
  if (!EMAIL || !PASSWORD) throw new Error('Pass --email and --password (or LT_PASSWORD) of an administrator account');
  const health = await (await fetch(`${URL}/api/health`)).json();
  await signIn();
  if (!health.live_running) {
    await api('/api/live/start', { method: 'POST', headers: { 'content-type': 'application/json', 'x-csrf-token': csrf },
      body: JSON.stringify({ scenario: 'emergency', controller: 'adaptive_ev_vanet', nav: 'none', seed: 1 }) });
    await sleep(3000);
  }
  const result = {
    date: new Date().toISOString(), url: URL, mock: health.mock, max_connections: health.max_connections, hold_s: HOLD_S, api_rps_target: RPS,
    hardware: { platform: `${os.type()} ${os.release()}`, cpu: os.cpus()[0]?.model?.trim(), threads: os.cpus().length,
      memory_gb: +(os.totalmem() / 2 ** 30).toFixed(1), node: process.version },
    steps: [],
  };
  const log = (...a) => console.error(...a);

  for (const n of STEPS) {
    const sink = { on: false, latency: [], states: 0, drops: 0 };
    const { sockets, outcome } = await openMany(n, Math.min(20_000, n * 20), sink);
    const h = await hold(HOLD_S, sink);
    const row = { users: n, connected: outcome.connected, refused: outcome.refused, failed: outcome.failed,
      success_pct: +((100 * outcome.connected) / n).toFixed(2), connect_ms: stats(outcome.connect_ms),
      drops_during_hold: sink.drops, states_per_user_per_s: +(sink.states / Math.max(1, outcome.connected) / h.seconds).toFixed(2),
      stream_latency_ms: stats(sink.latency), ...h };
    result.steps.push(row);
    log(`step ${n}: connected ${row.connected}/${n}, stream p95 ${row.stream_latency_ms.p95} ms, API p95 ${row.api.p95} ms p99 ${row.api.p99} ms, `
      + `CPU ${row.server.cpu_pct_one_core} %, RSS ${row.server.rss_mb} MB, loop lag p99 ${row.server.event_loop_lag_ms.p99} ms`);
    if (n === STEPS.at(-1) && RECONNECT > 0) {
      // Reconnection: drop some viewers at once and bring them straight back
      const victims = sockets.splice(0, Math.min(RECONNECT, sockets.length));
      victims.forEach((s) => s.close());
      await sleep(500);
      const t0 = performance.now();
      const back = await openMany(victims.length, 0, { on: false, latency: [], states: 0, drops: 0 });
      result.reconnect = { dropped: victims.length, reconnected: back.outcome.connected, refused: back.outcome.refused, failed: back.outcome.failed,
        all_back_after_ms: +(performance.now() - t0).toFixed(0), connect_ms: stats(back.outcome.connect_ms),
        viewers_after: (await api('/api/metrics')).body.viewers };
      log(`reconnect: ${result.reconnect.reconnected}/${victims.length} back after ${result.reconnect.all_back_after_ms} ms`);
      sockets.push(...back.sockets);
    }
    if (n === STEPS.at(-1) && SOAK_MIN > 0) {
      // Soak: keep the last step connected and sample once a minute
      result.soak = { minutes: SOAK_MIN, users: outcome.connected, samples: [] };
      const sinkS = { on: false, latency: [], states: 0, drops: 0 };
      sockets.forEach((s) => {
        s.on('state', (m) => { if (sinkS.on && m.sent_at) sinkS.latency.push(Date.now() - m.sent_at); });
        s.on('disconnect', () => { if (sinkS.on) sinkS.drops += 1; });
      });
      for (let minute = 1; minute <= SOAK_MIN; minute += 1) {
        sinkS.latency = [];
        const hs = await hold(60, sinkS);
        const sample = { minute, viewers: hs.server.viewers, drops_total: sinkS.drops, stream_p95_ms: pct(sinkS.latency, 95), stream_p99_ms: pct(sinkS.latency, 99),
          api_p95_ms: hs.api.p95, api_p99_ms: hs.api.p99, api_errors: hs.api.errors, cpu_pct: hs.server.cpu_pct_one_core, rss_mb: hs.server.rss_mb,
          heap_mb: hs.server.heap_mb, loop_lag_p99_ms: hs.server.event_loop_lag_ms.p99, persist_errors: hs.server.persist_errors };
        result.soak.samples.push(sample);
        log(`soak minute ${minute}/${SOAK_MIN}: viewers ${sample.viewers}, drops ${sample.drops_total}, stream p95 ${sample.stream_p95_ms} ms, `
          + `API p95 ${sample.api_p95_ms} ms, RSS ${sample.rss_mb} MB, heap ${sample.heap_mb} MB`);
        if (OUT) fs.writeFileSync(OUT, JSON.stringify(result, null, 2));
      }
      const sim = (await api('/api/live/status')).body;
      result.soak.simulation_at_end = { running: sim.running, lifecycle: sim.lifecycle };
    }
    await closeAll(sockets);
  }

  if (SPIKE > 0) {
    // Spike: every connection at the same moment
    const sink = { on: false, latency: [], states: 0, drops: 0 };
    const t0 = performance.now();
    const { sockets, outcome } = await openMany(SPIKE, 0, sink);
    const allMs = performance.now() - t0;
    const h = await hold(Math.min(HOLD_S, 15), sink);
    result.spike = { users: SPIKE, connected: outcome.connected, refused: outcome.refused, failed: outcome.failed,
      success_pct: +((100 * outcome.connected) / SPIKE).toFixed(2), all_connected_after_ms: +allMs.toFixed(0), connect_ms: stats(outcome.connect_ms),
      drops_during_hold: sink.drops, stream_latency_ms: stats(sink.latency), ...h };
    log(`spike ${SPIKE}: connected ${outcome.connected}, refused ${outcome.refused}, all in ${result.spike.all_connected_after_ms} ms, `
      + `API p95 ${h.api.p95} ms, loop lag p99 ${h.server.event_loop_lag_ms.p99} ms`);
    await closeAll(sockets);
  }

  if (LOGINS > 0) {
    // Sign-in burst: password hashing is deliberately slow, so this is the costliest request
    const t0 = performance.now();
    const ms = [];
    let fail = 0;
    await Promise.all(Array.from({ length: LOGINS }, async () => {
      const s0 = performance.now();
      const res = await fetch(`${URL}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: EMAIL, password: PASSWORD }) });
      if (res.ok) ms.push(performance.now() - s0);
      else fail += 1;
    }));
    const total = performance.now() - t0;
    const probe = await api('/api/live/status');
    result.logins = { concurrent: LOGINS, ok: ms.length, failed: fail, total_ms: +total.toFixed(0), per_second: +((ms.length / total) * 1000).toFixed(1),
      latency_ms: stats(ms), api_probe_after_ms: +probe.ms.toFixed(1) };
    log(`logins ${LOGINS}: ${ms.length} ok in ${result.logins.total_ms} ms (${result.logins.per_second}/s), p95 ${result.logins.latency_ms.p95} ms`);
  }

  if (OUT) fs.writeFileSync(OUT, JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
  process.exit(0);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
