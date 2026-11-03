// End-to-end smoke test with the REAL simulation (SUMO + Python): only runs when E2E_REAL=1.
//   npm run test:e2e   (from the project root)
import fs from 'node:fs';
import path from 'node:path';
import request from 'supertest';
import { io as ioc } from 'socket.io-client';
import { describe, expect, it } from 'vitest';
import { createGateway } from '../src/app.js';
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
      USE_MOCK: 'false', PYTHON_BIN: PYTHON, ADMIN_TOKEN: 'e2e', LIVE_LOOP: 'false', DB_PATH: ':memory:',
      SIM_STEP_DELAY: '0', SIM_MAX_STEPS: '260', STATE_INTERVAL_MS: '200', LOG_LEVEL: 'warn',
      PLAYLIST_PATH: path.join(SERVER_ROOT, 'no-playlist.json'),
    });
    const addr = await gw.listen(0, '127.0.0.1');
    const socket = ioc(`http://127.0.0.1:${addr.port}`, { transports: ['websocket'], forceNew: true });
    const got = { state: 0, kinds: new Set(), result: null, network: null };
    socket.on('state', () => { got.state += 1; });
    socket.on('event', (e) => got.kinds.add(e.kind));
    socket.on('result', (r) => { got.result = r; });
    socket.on('network', (n) => { got.network = n; });
    try {
      await new Promise((r) => socket.on('init', r));
      const res = await request(gw.app).post('/api/live/start').set('X-Admin-Token', 'e2e')
        .send({ scenario: 'emergency', network: 'grid2x2', controller: 'adaptive_ev_vanet', seed: 1 }).expect(202);
      await waitFor(() => got.result !== null, 170_000);

      expect(got.state).toBeGreaterThan(10);
      expect(got.network?.junctions.filter((j) => j.tls).length).toBe(4);
      expect(got.kinds.has('alert_delivered')).toBe(true);
      expect(got.kinds.has('preempt_start')).toBe(true);
      expect(got.result.metrics.missed_preemptions).toBe(0);

      await waitFor(async () => (await request(gw.app).get(`/api/runs/${res.body.run_id}`)).body.run?.status === 'finished', 20_000);
      const stored = (await request(gw.app).get(`/api/runs/${res.body.run_id}`)).body;
      expect(stored.result.metrics).toHaveProperty('ev_travel_time');
      expect(stored.events.some((e) => e.kind === 'preempt_start')).toBe(true);
    } finally {
      socket.close();
      await gw.close();
    }
  }, 200_000);
});
