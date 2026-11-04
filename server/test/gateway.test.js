// Gateway tests against the mock simulation (USE_MOCK=true) and an in-memory database.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import request from 'supertest';
import { io as ioc } from 'socket.io-client';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { createGateway } from '../src/app.js';
import { buildSimArgs, parseLine, RunEntrySchema } from '../src/protocol.js';

const TOKEN = 'test-token';
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-test-'));

const baseOverrides = {
  USE_MOCK: 'true',
  ADMIN_TOKEN: TOKEN,
  LIVE_LOOP: 'false',
  DB_PATH: ':memory:',
  MOCK_STEP_DELAY: '0.05',
  MOCK_MAX_STEPS: '60',
  STATE_INTERVAL_MS: '200',
  VIEWER_BROADCAST_MS: '100',
  CRASH_RETRY_MS: '200',
  PLAYLIST_PATH: path.join(tmp, 'none.json'),
  SUMMARY_PATH: path.join(tmp, 'summary.json'),
  LOG_LEVEL: 'silent',
};

const waitFor = async (pred, timeoutMs = 20_000, stepMs = 50) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await pred()) return true;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  throw new Error('waitFor timed out');
};

function viewer(url) {
  const socket = ioc(url, { transports: ['websocket'], forceNew: true, reconnection: false });
  const got = { init: null, state: [], event: [], status: [], result: [], now_playing: [] };
  socket.on('init', (m) => (got.init = m));
  for (const name of ['state', 'event', 'status', 'result', 'now_playing']) socket.on(name, (m) => got[name].push(m));
  return { socket, got };
}

async function startGateway(extra = {}) {
  const gw = createGateway({ ...baseOverrides, ...extra });
  const addr = await gw.listen(0, '127.0.0.1');
  return { gw, url: `http://127.0.0.1:${addr.port}` };
}

describe('protocol line parsing', () => {
  it('ignores bad lines and normalises old-style events', () => {
    expect(parseLine('not json')).toBeNull();
    expect(parseLine('{"type":"unknown"}')).toBeNull();
    expect(parseLine('')).toBeNull();
    const ev = parseLine('{"type":"event","event":"alert_sent","tls_id":"J1","t":3}');
    expect(ev).toMatchObject({ kind: 'alert_sent', tls: 'J1', detail: '' });
  });
});

describe('gateway with mock simulation', () => {
  let gw;
  let url;
  const sockets = [];

  beforeAll(async () => {
    ({ gw, url } = await startGateway());
  });
  afterEach(() => {
    while (sockets.length) sockets.pop().close();
  });
  afterAll(async () => {
    await gw.close();
  });

  it('serves health, options, playlist and status', async () => {
    const health = await request(gw.app).get('/api/health').expect(200);
    expect(health.body).toMatchObject({ ok: true, mock: true, live_running: false });
    const opts = await request(gw.app).get('/api/options').expect(200);
    expect(opts.body.controllers).toContain('adaptive_ev_vanet');
    expect(opts.body.nav).toContain('aco');
    await request(gw.app).get('/api/live/status').expect(200);
    await request(gw.app).get('/api/nope').expect(404);
  });

  it('requires the admin token and validates the body', async () => {
    const body = { scenario: 'emergency', controller: 'adaptive_ev_vanet' };
    await request(gw.app).post('/api/live/start').send(body).expect(401);
    await request(gw.app).post('/api/live/start').set('X-Admin-Token', 'wrong').send(body).expect(401);
    await request(gw.app).post('/api/live/stop').expect(401);
    const bad = await request(gw.app).post('/api/live/start').set('X-Admin-Token', TOKEN)
      .send({ scenario: 'mars', controller: 'adaptive' }).expect(400);
    expect(bad.body.issues[0].path).toBe('scenario');
    await request(gw.app).post('/api/live/start').set('X-Admin-Token', TOKEN)
      .send({ ...body, shell: 'rm -rf /' }).expect(400); // unknown keys are rejected
  });

  it('broadcasts one live run to all viewers, gives late viewers history, and stores the result', async () => {
    const early = viewer(url);
    sockets.push(early.socket);
    await waitFor(() => early.got.init !== null);
    expect(early.got.init.states).toEqual([]);

    const res = await request(gw.app).post('/api/live/start').set('X-Admin-Token', TOKEN)
      .send({ scenario: 'emergency', controller: 'adaptive_ev_vanet', seed: 2, relay: 'on' }).expect(202);
    const runId = res.body.run_id;

    await waitFor(() => early.got.state.length >= 4);
    const late = viewer(url);
    sockets.push(late.socket);
    await waitFor(() => late.got.init !== null);
    expect(late.got.init.now_playing.current.run_id).toBe(runId);
    expect(late.got.init.states.length).toBeGreaterThan(5); // history arrives at once
    expect(late.got.init.states.every((s) => s.run_id === runId)).toBe(true);
    await waitFor(() => late.got.state.length >= 1);

    // throttling: the mock emits ~20 states/s, the gateway broadcasts at most ~5/s (200 ms)
    await waitFor(() => early.got.result.length === 1);
    const st = early.got.state;
    for (let i = 1; i < st.length; i += 1) expect(st[i].sent_at - st[i - 1].sent_at).toBeGreaterThanOrEqual(190);
    expect(st[0]).toHaveProperty('tls');
    expect(st[0]).toHaveProperty('comm');
    expect(st[0]).toHaveProperty('nav');
    expect(early.got.event.some((e) => e.kind === 'preempt_start')).toBe(true);
    expect(early.got.status.some((s) => s.status === 'finished')).toBe(true);

    await waitFor(async () => (await request(gw.app).get(`/api/runs/${runId}`)).body.run?.status === 'finished');
    const stored = (await request(gw.app).get(`/api/runs/${runId}?samples=5`).expect(200)).body;
    expect(stored.result.metrics).toHaveProperty('avg_waiting_time');
    expect(stored.events.length).toBeGreaterThan(0);
    expect(stored.state_samples).toBeGreaterThan(10); // one per 2 simulated seconds
    expect(stored.samples.length).toBe(5);
    const list = (await request(gw.app).get('/api/runs').expect(200)).body.runs;
    expect(list[0]).toMatchObject({ id: runId, has_result: true });
    await request(gw.app).get('/api/runs/does-not-exist').expect(404);
  });

  it('streams the road map and car positions to everyone, and car details only to followers', async () => {
    const a = viewer(url);
    const b = viewer(url);
    sockets.push(a.socket, b.socket);
    const vehiclesA = [];
    const detailA = [];
    const detailB = [];
    a.socket.on('vehicles', (m) => vehiclesA.push(m));
    a.socket.on('vehicle', (m) => detailA.push(m));
    b.socket.on('vehicle', (m) => detailB.push(m));
    let network = null;
    a.socket.on('network', (m) => (network = m));
    await waitFor(() => a.got.init && b.got.init);

    await request(gw.app).post('/api/live/start').set('X-Admin-Token', TOKEN)
      .send({ scenario: 'normal', controller: 'fixed', eco_fraction: 0.6 }).expect(202);
    await waitFor(() => network !== null && vehiclesA.some((m) => m.rows.length > 2));
    expect(network.edges.length).toBeGreaterThan(0);
    const row = vehiclesA.at(-1).rows[0];
    expect(row).toHaveLength(5); // [id, x, y, angle, flags] only
    expect(typeof row[1]).toBe('number');

    const id = row[0];
    a.socket.emit('follow', id);
    await waitFor(() => detailA.some((d) => d.id === id && 'co2_g' in d && 'tls' in d));
    // The detail keeps streaming (one per map frame), not just once on follow
    const n0 = detailA.length;
    await waitFor(() => detailA.length >= n0 + 3);
    expect(new Set(detailA.map((d) => d.t)).size).toBeGreaterThan(2);
    expect(detailB).toHaveLength(0); // B follows nobody
    a.socket.emit('follow', '../etc/passwd'); // invalid ids are ignored
    a.socket.emit('unfollow');
    const late = viewer(url);
    sockets.push(late.socket);
    await waitFor(() => late.got.init !== null);
    expect(late.got.init.network.edges.length).toBe(network.edges.length);
    expect(late.got.init.vehicles.rows.length).toBeGreaterThan(0);
    await request(gw.app).post('/api/live/stop').set('X-Admin-Token', TOKEN).expect(200);
  }, 30_000);

  it('stops a running simulation on admin request', async () => {
    const v = viewer(url);
    sockets.push(v.socket);
    const res = await request(gw.app).post('/api/live/start').set('X-Admin-Token', TOKEN)
      .send({ scenario: 'normal', controller: 'fixed' }).expect(202);
    await waitFor(() => v.got.state.some((s) => s.run_id === res.body.run_id));
    const stop = await request(gw.app).post('/api/live/stop').set('X-Admin-Token', TOKEN).expect(200);
    expect(stop.body.stopped).toBe(true);
    await waitFor(() => v.got.status.some((s) => s.run_id === res.body.run_id && s.status === 'stopped'));
    const run = (await request(gw.app).get(`/api/runs/${res.body.run_id}`)).body.run;
    expect(run.status).toBe('stopped');
    expect((await request(gw.app).get('/api/live/status')).body.running).toBe(false);
  });

  it('guards the SUMO-GUI toggle and refuses it with the mock', async () => {
    const info = (await request(gw.app).get('/api/sumo/gui').expect(200)).body;
    expect(info).toMatchObject({ available: false, enabled: false, mock: true });
    // A request that came through a proxy is not "local": without the admin token it is refused
    await request(gw.app).post('/api/sumo/gui').set('X-Forwarded-For', '203.0.113.9').send({ enabled: true }).expect(403);
    // Local, but the mock simulation has no SUMO window
    const mock = await request(gw.app).post('/api/sumo/gui').send({ enabled: true }).expect(409);
    expect(mock.body.error).toMatch(/mock/);
  });

  it('adds --gui to the simulation arguments only when the window is on', () => {
    const entry = { scenario: 'normal', controller: 'fixed', nav: 'none', seed: 1, penetration: 1, loss: 0, relay: 'off', eco_fraction: 0 };
    const real = { ...gw.cfg, USE_MOCK: false, SIM_GUI: false };
    expect(buildSimArgs(entry, 'r1', real)).not.toContain('--gui');
    expect(buildSimArgs(entry, 'r1', real, { gui: true })).toContain('--gui');
    expect(buildSimArgs(entry, 'r1', { ...gw.cfg, USE_MOCK: true }, { gui: true })).not.toContain('--gui');
  });

  it('passes ambulance routing to the simulation and rejects bad values', () => {
    const base = { scenario: 'ev_incident', controller: 'adaptive_ev_vanet' };
    const parsed = RunEntrySchema.parse(base);
    expect(parsed.ev_routing).toBe('off');
    const args = buildSimArgs(RunEntrySchema.parse({ ...base, ev_routing: 'on' }), 'r1', { ...gw.cfg, USE_MOCK: false });
    expect(args[args.indexOf('--ev-routing') + 1]).toBe('on');
    expect(RunEntrySchema.safeParse({ ...base, ev_routing: 'yes' }).success).toBe(false);
  });

  it('returns 404 until the experiment summary exists', async () => {
    const missing = await request(gw.app).get('/api/experiments/summary').expect(404);
    expect(missing.body.error).toMatch(/evaluate\.py/);
    fs.writeFileSync(baseOverrides.SUMMARY_PATH, JSON.stringify({ generated_at: 'x', suites: {} }));
    const ok = await request(gw.app).get('/api/experiments/summary').expect(200);
    expect(ok.body).toHaveProperty('suites');
  });
});

describe('playlist loop', () => {
  it('advances through the playlist and recovers from a crashed run', async () => {
    const playlist = path.join(tmp, 'playlist.json');
    fs.writeFileSync(playlist, JSON.stringify([
      { scenario: 'normal', controller: 'fixed' },
      { scenario: 'emergency', controller: 'adaptive_ev' },
    ]));
    const { gw, url } = await startGateway({ LIVE_LOOP: 'true', PLAYLIST_PATH: playlist, MOCK_MAX_STEPS: '8' });
    const v = viewer(url);
    try {
      await waitFor(() => v.got.init !== null);
      gw.live.startLoop();
      await waitFor(() => v.got.now_playing.filter((n) => n.current?.entry?.scenario === 'emergency').length > 0);
      const np = v.got.now_playing.find((n) => n.running && n.current?.entry?.scenario === 'normal');
      expect(np.next.scenario).toBe('emergency');

      // Crash: point the mock entry at a missing file -> Python exits non-zero -> next entry after CRASH_RETRY_MS
      gw.cfg.MOCK_ENTRY = 'sim/does_not_exist.py';
      await waitFor(() => gw.db.listRuns(50).some((r) => r.status === 'error'));
      const before = gw.db.listRuns(50).length;
      await waitFor(() => gw.db.listRuns(50).length > before);
    } finally {
      v.socket.close();
      await gw.close();
    }
  });

  it('marks runs left running by a previous gateway as interrupted', async () => {
    const dbPath = path.join(tmp, 'restart.db');
    const first = createGateway({ ...baseOverrides, DB_PATH: dbPath });
    first.db.insertRun({ id: 'r-old', scenario: 'normal', controller: 'fixed', nav: 'none', seed: 1,
      penetration: 1, loss: 0, relay: 'off', source: 'admin' });
    first.db.close();
    const second = createGateway({ ...baseOverrides, DB_PATH: dbPath });
    try {
      expect(second.db.getRun('r-old').run.status).toBe('interrupted');
    } finally {
      await second.close();
    }
  });
});
