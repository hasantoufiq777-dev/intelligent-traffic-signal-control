// End-to-end smoke test with the REAL simulation (SUMO + Python): only runs when E2E_REAL=1.
//   npm run test:e2e   (from the project root)
import fs from 'node:fs';
import path from 'node:path';
import request from 'supertest';
import { describe, expect, it } from 'vitest';
import { createGateway } from '../src/app.js';
import { adminSession, connect, TEST_ENV } from './helpers.js';
import { SERVER_ROOT } from '../src/config.js';

const enabled = process.env.E2E_REAL === '1';
const win = process.platform === 'win32';
const venvPython = path.resolve(SERVER_ROOT, '..', '.venv', win ? 'Scripts' : 'bin', win ? 'python.exe' : 'python');
const PYTHON = process.env.PYTHON_BIN || (fs.existsSync(venvPython) ? venvPython : win ? 'python' : 'python3');

const waitFor = async (pred, timeoutMs, stepMs = 200) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await pred()) return;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  throw new Error('waitFor timed out');
};

describe.skipIf(!enabled)('end-to-end with the real SUMO simulation', () => {
  it('runs an ambulance scenario on the grid: states, V2I alerts, preemption, stored result', async () => {
    const gw = createGateway({
      USE_MOCK: 'false', PYTHON_BIN: PYTHON, ...TEST_ENV, LIVE_LOOP: 'false', DB_PATH: ':memory:',
      SIM_STEP_DELAY: '0', SIM_MAX_STEPS: '260', STATE_INTERVAL_MS: '200', LOG_LEVEL: 'warn',
      PLAYLIST_PATH: path.join(SERVER_ROOT, 'no-playlist.json'),
    });
    const addr = await gw.listen(0, '127.0.0.1');
    const S = await adminSession(gw);
    const socket = connect(`http://127.0.0.1:${addr.port}`, S);
    const got = { state: 0, kinds: new Set(), result: null, network: null };
    socket.on('state', () => { got.state += 1; });
    socket.on('event', (e) => got.kinds.add(e.kind));
    socket.on('result', (r) => { got.result = r; });
    socket.on('network', (n) => { got.network = n; });
    try {
      await new Promise((r) => socket.on('init', r));
      const res = await S.post('/api/live/start')
        .send({ scenario: 'emergency', network: 'grid2x2', controller: 'adaptive_ev_vanet', seed: 1 }).expect(202);
      await waitFor(() => got.result !== null, 170_000);

      expect(got.state).toBeGreaterThan(10);
      expect(got.network?.junctions.filter((j) => j.tls).length).toBe(4);
      expect(got.kinds.has('alert_delivered')).toBe(true);
      expect(got.kinds.has('preempt_start')).toBe(true);
      expect(got.result.metrics.missed_preemptions).toBe(0);

      await waitFor(async () => (await S.get(`/api/runs/${res.body.run_id}`)).body.run?.status === 'finished', 20_000);
      const stored = (await S.get(`/api/runs/${res.body.run_id}`)).body;
      expect(stored.result.metrics).toHaveProperty('ev_travel_time');
      expect(stored.events.some((e) => e.kind === 'preempt_start')).toBe(true);
    } finally {
      socket.close();
      await gw.close();
    }
  }, 200_000);

  it('lets a viewer drive a car through the grid and stores the trip report', async () => {
    const gw = createGateway({
      USE_MOCK: 'false', PYTHON_BIN: PYTHON, ...TEST_ENV, LIVE_LOOP: 'false', DB_PATH: ':memory:',
      SIM_STEP_DELAY: '0.02', DRIVER_STEP_DELAY: '0.02', SIM_MAX_STEPS: '320', STATE_INTERVAL_MS: '200',
      LOG_LEVEL: 'warn', PLAYLIST_PATH: path.join(SERVER_ROOT, 'no-playlist.json'),
    });
    const addr = await gw.listen(0, '127.0.0.1');
    const S = await adminSession(gw);
    const socket = connect(`http://127.0.0.1:${addr.port}`, S);
    const drv = [];
    const seat = [];
    socket.on('driver', (m) => drv.push(m));
    socket.on('driver_seat', (m) => seat.push(m));
    const ask = (name, body) => new Promise((resolve) => socket.emit(name, body, resolve));
    try {
      await new Promise((r) => socket.on('init', r));
      await S.post('/api/live/start')
        .send({ scenario: 'emergency', network: 'grid2x2', controller: 'adaptive_ev_vanet', seed: 1 }).expect(202);
      await waitFor(() => gw.live.network !== null && gw.live.driver.public().available, 60_000);
      expect((await ask('driver:claim', { from: 'bottom0J0', to: 'J3top1' })).ok).toBe(true);
      await waitFor(() => drv.some((m) => m.status === 'driving'), 30_000);
      expect((await ask('driver:cmd', { action: 'ease', speed_kmh: 25 })).ok).toBe(true);
      await waitFor(() => drv.some((m) => m.mode === 'ease' && Math.abs(m.speed_kmh - 25) < 0.5), 30_000);
      await waitFor(() => seat.some((s) => s.reason === 'finished'), 120_000);
      const final = drv.find((m) => m.status === 'finished');
      expect(final.report).toMatchObject({ finished: true, from: 'bottom0J0', to: 'J3top1' });
      expect(final.report.compare).not.toBeNull();
      const trip = (await S.get('/api/driver')).body.trips[0];
      expect(trip).toMatchObject({ status: 'finished', reason: 'finished', vid: 'drv1' });
      expect(trip.report.distance_m).toBeGreaterThan(400);
    } finally {
      socket.close();
      await gw.close();
    }
  }, 200_000);
});
