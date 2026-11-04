// One-driver mode against the mock simulation: seat lock, commands, rate limit, idle and
// disconnect release, admin control, and the stored trip with its report.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import request from 'supertest';
import { io as ioc } from 'socket.io-client';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { createGateway } from '../src/app.js';
import { buildSimArgs, DriverCommandSchema, DriverStartSchema } from '../src/protocol.js';

const TOKEN = 'test-token';
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-driver-'));
const FLAG_DRIVER = 32;

const base = {
  USE_MOCK: 'true', ADMIN_TOKEN: TOKEN, LIVE_LOOP: 'false', DB_PATH: ':memory:',
  MOCK_STEP_DELAY: '0.05', MOCK_MAX_STEPS: '400', STATE_INTERVAL_MS: '200', VIEWER_BROADCAST_MS: '100',
  CRASH_RETRY_MS: '200', PLAYLIST_PATH: path.join(tmp, 'none.json'), SUMMARY_PATH: path.join(tmp, 's.json'),
  LOG_LEVEL: 'silent', DRIVER_RESUME_MS: '0',
};

const waitFor = async (pred, timeoutMs = 20_000, stepMs = 40) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await pred()) return true;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  throw new Error('waitFor timed out');
};

function viewer(url) {
  const socket = ioc(url, { transports: ['websocket'], forceNew: true, reconnection: false });
  const got = { init: null, driver: [], lock: [], seat: [], vehicles: [], event: [] };
  socket.on('init', (m) => (got.init = m));
  socket.on('driver', (m) => got.driver.push(m));
  socket.on('driver_lock', (m) => got.lock.push(m));
  socket.on('driver_seat', (m) => got.seat.push(m));
  socket.on('vehicles', (m) => got.vehicles.push(m));
  socket.on('event', (m) => got.event.push(m));
  const ask = (name, body) => new Promise((resolve) => socket.emit(name, body, resolve));
  return { socket, got, ask };
}

async function startGateway(extra = {}) {
  const gw = createGateway({ ...base, ...extra });
  const addr = await gw.listen(0, '127.0.0.1');
  return { gw, url: `http://127.0.0.1:${addr.port}` };
}

async function startRun(gw, entry = { scenario: 'navigation', controller: 'fixed' }) {
  const res = await request(gw.app).post('/api/live/start').set('X-Admin-Token', TOKEN).send(entry).expect(202);
  await waitFor(() => gw.live.network !== null && gw.live.driver.public().available);
  return res.body.run_id;
}

describe('driver message schemas', () => {
  it('accept roads and commands and reject the rest', () => {
    expect(DriverStartSchema.safeParse({ from: 'bottom0J0', to: 'J3top1' }).success).toBe(true);
    expect(DriverStartSchema.safeParse({ from: ':J0_1', to: 'J3top1' }).success).toBe(false); // junction-internal lane
    expect(DriverStartSchema.safeParse({ from: 'a', to: 'a' }).success).toBe(false);
    expect(DriverStartSchema.safeParse({ from: 'a', to: 'b', x: 1 }).success).toBe(false);
    expect(DriverCommandSchema.safeParse({ action: 'ease', speed_kmh: 30 }).success).toBe(true);
    expect(DriverCommandSchema.safeParse({ action: 'ease', speed_kmh: 200 }).success).toBe(false);
    expect(DriverCommandSchema.safeParse({ action: 'teleport' }).success).toBe(false);
  });

  it('starts the simulation with the command channel', () => {
    const entry = { scenario: 'normal', controller: 'fixed', nav: 'none', seed: 1, penetration: 1, loss: 0, relay: 'off' };
    expect(buildSimArgs(entry, 'r1', { USE_MOCK: false, SIM_ENTRY: 'sim/main.py', SIM_STEP_DELAY: 0 })).toContain('--driver');
  });
});

describe('one-driver mode with the mock simulation', () => {
  let gw;
  let url;
  const sockets = [];

  beforeAll(async () => {
    ({ gw, url } = await startGateway({ DRIVER_CMDS_PER_S: '2' }));
  });
  afterEach(async () => {
    while (sockets.length) sockets.pop().close();
    await request(gw.app).post('/api/live/stop').set('X-Admin-Token', TOKEN);
  });
  afterAll(async () => {
    await gw.close();
  });

  it('gives the seat to one viewer, accepts only their commands, rate limits them, and stores the trip', async () => {
    const a = viewer(url);
    const b = viewer(url);
    sockets.push(a.socket, b.socket);
    await waitFor(() => a.got.init && b.got.init);

    // No live run yet
    expect((await a.ask('driver:claim', { from: 'bottom0J0', to: 'J3top1' })).ok).toBe(false);

    const runId = await startRun(gw);
    expect((await a.ask('driver:claim', { from: 'nowhere', to: 'J3top1' })).error).toMatch(/Unknown road/);
    expect((await a.ask('driver:claim', {})).ok).toBe(false);
    const claim = await a.ask('driver:claim', { from: 'bottom0J0', to: 'J3top1' });
    expect(claim).toMatchObject({ ok: true, idle_ms: 120_000 });
    expect(claim.key).toMatch(/^[0-9a-f]{32}$/);

    const busy = await b.ask('driver:claim', { from: 'left0J0', to: 'J3right1' });
    expect(busy).toMatchObject({ ok: false });
    expect(busy.error).toMatch(/Someone else is driving/);
    expect((await b.ask('driver:cmd', { action: 'stop' })).error).toMatch(/not driving/);
    expect((await b.ask('driver:release')).ok).toBe(false);

    // Everyone sees the seat taken and the driven car (flag 32) on the map
    await waitFor(() => b.got.lock.some((l) => l.busy && l.vid));
    const vid = b.got.lock.findLast((l) => l.busy).vid;
    await waitFor(() => b.got.vehicles.some((m) => m.rows.some((r) => r[0] === vid && (r[4] & FLAG_DRIVER))));
    await waitFor(() => b.got.driver.some((d) => d.vid === vid && d.status === 'driving'));

    // Commands reach the car: ease off to 18 km/h
    expect(await a.ask('driver:cmd', { action: 'ease', speed_kmh: 18 })).toEqual({ ok: true });
    await waitFor(() => a.got.driver.some((d) => d.mode === 'ease' && d.target_kmh === 18 && d.speed_kmh === 18));
    expect((await a.ask('driver:cmd', { action: 'fly' })).error).toMatch(/Invalid command/);

    // Rate limit: burst of 4 (2/s), then refused with a retry time
    const replies = [];
    for (let i = 0; i < 8; i += 1) replies.push(await a.ask('driver:cmd', { action: 'go' }));
    const refused = replies.filter((r) => !r.ok);
    expect(refused.length).toBeGreaterThan(0);
    expect(refused[0].error).toMatch(/Too many commands/);
    expect(refused[0].retry_ms).toBeGreaterThan(0);

    // Hand back: seat free, owner told, trip stored with the simulation's report
    expect((await a.ask('driver:release')).ok).toBe(true);
    await waitFor(() => a.got.seat.some((s) => s.mine === false && s.reason === 'released'));
    expect(gw.live.driver.public().busy).toBe(false);
    await waitFor(() => a.got.driver.some((d) => d.vid === vid && d.status === 'released' && d.report));
    await waitFor(async () => (await request(gw.app).get('/api/driver')).body.trips[0]?.report !== null);
    const info = (await request(gw.app).get('/api/driver').expect(200)).body;
    expect(info).toMatchObject({ enabled: true, busy: false });
    expect(info.trips[0]).toMatchObject({ run_id: runId, vid, from_edge: 'bottom0J0', to_edge: 'J3top1', status: 'released', reason: 'released' });
    expect(info.trips[0].report).toHaveProperty('travel_s');
    expect(a.got.event.some((e) => e.kind === 'driver_start')).toBe(true);

    // The seat is free again for the next viewer
    expect((await b.ask('driver:claim', { from: 'left0J0', to: 'J3right1' })).ok).toBe(true);
  }, 40_000);

  it('drives to the destination and stores the finished trip report', async () => {
    const a = viewer(url);
    sockets.push(a.socket);
    await waitFor(() => a.got.init);
    await startRun(gw, { scenario: 'normal', controller: 'fixed' }); // single junction: S -> J0 -> N
    expect((await a.ask('driver:claim', { from: 'S2J0', to: 'J02N' })).ok).toBe(true);
    await waitFor(() => a.got.seat.some((s) => s.reason === 'finished'), 30_000);
    const final = a.got.driver.find((d) => d.status === 'finished');
    expect(final.report).toMatchObject({ finished: true, from: 'S2J0', to: 'J02N' });
    expect(final.report.distance_m).toBeGreaterThan(400);
    expect(final.report.compare).toHaveProperty('co2_g_per_km');
    const trip = (await request(gw.app).get('/api/driver')).body.trips[0];
    expect(trip).toMatchObject({ status: 'finished', reason: 'finished' });
  }, 40_000);

  it('lets the admin switch driving off and end a drive', async () => {
    const a = viewer(url);
    sockets.push(a.socket);
    await waitFor(() => a.got.init);
    await startRun(gw);
    await request(gw.app).post('/api/driver').send({ enabled: false }).expect(401);
    await request(gw.app).post('/api/driver').set('X-Admin-Token', TOKEN).send({ enabled: 'no' }).expect(400);

    expect((await a.ask('driver:claim', { from: 'bottom0J0', to: 'J3top1' })).ok).toBe(true);
    const off = await request(gw.app).post('/api/driver').set('X-Admin-Token', TOKEN).send({ enabled: false }).expect(200);
    expect(off.body).toMatchObject({ enabled: false, busy: false });
    await waitFor(() => a.got.seat.some((s) => s.reason === 'admin'));
    expect((await a.ask('driver:claim', { from: 'bottom0J0', to: 'J3top1' })).error).toMatch(/switched off/);

    await request(gw.app).post('/api/driver').set('X-Admin-Token', TOKEN).send({ enabled: true }).expect(200);
    expect((await a.ask('driver:claim', { from: 'bottom0J0', to: 'J3top1' })).ok).toBe(true);
    const kick = await request(gw.app).post('/api/driver/release').set('X-Admin-Token', TOKEN).expect(200);
    expect(kick.body.released).toBe(true);
    expect(gw.live.driver.public().busy).toBe(false);
  }, 40_000);

  it('ends the drive when the run is replaced', async () => {
    const a = viewer(url);
    sockets.push(a.socket);
    await waitFor(() => a.got.init);
    await startRun(gw);
    expect((await a.ask('driver:claim', { from: 'bottom0J0', to: 'J3top1' })).ok).toBe(true);
    await startRun(gw, { scenario: 'normal', controller: 'fixed' });
    await waitFor(() => a.got.seat.some((s) => s.reason === 'run_ended'));
    expect(gw.live.driver.public().busy).toBe(false);
  }, 40_000);
});

describe('driver seat timeouts', () => {
  it('frees the seat after the idle time, and when the driver leaves (after the reconnect grace)', async () => {
    const { gw, url } = await startGateway({ DRIVER_IDLE_MS: '1000', DRIVER_RESUME_MS: '700' });
    const a = viewer(url);
    try {
      await waitFor(() => a.got.init);
      await startRun(gw);
      expect((await a.ask('driver:claim', { from: 'bottom0J0', to: 'J3top1' })).ok).toBe(true);
      await waitFor(() => a.got.seat.some((s) => s.reason === 'idle'), 5000);
      const idleTrip = gw.db.listDriverTrips(1)[0];
      expect(idleTrip).toMatchObject({ status: 'released', reason: 'idle' });

      // Disconnect and come back within the grace with the key: still driving
      const claim = await a.ask('driver:claim', { from: 'bottom0J0', to: 'J3top1' });
      expect(claim.ok).toBe(true);
      a.socket.close();
      const b = viewer(url);
      await waitFor(() => b.got.init);
      expect((await b.ask('driver:resume', 'wrong-key')).ok).toBe(false);
      expect((await b.ask('driver:resume', claim.key)).ok).toBe(true);
      expect((await b.ask('driver:cmd', { action: 'stop' })).ok).toBe(true);

      // Leave without coming back: freed after the grace
      b.socket.close();
      await waitFor(() => !gw.live.driver.public().busy, 5000);
      expect(gw.db.listDriverTrips(1)[0].reason).toBe('left');
    } finally {
      a.socket.close();
      await gw.close();
    }
  }, 40_000);
});
