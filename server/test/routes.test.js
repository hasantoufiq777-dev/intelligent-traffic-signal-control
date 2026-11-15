// Route explorer over the live socket: a viewer asks for A* alternatives between two roads; the gateway
// forwards the question to the simulation and answers only that viewer. Read-only, rate limited.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createGateway } from '../src/app.js';
import { connect, sessionFor, TEST_ENV } from './helpers.js';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-routes-'));
const waitFor = async (pred, timeoutMs = 15_000, stepMs = 25) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await pred()) return true;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  throw new Error('waitFor timed out');
};
const ask = (socket, body) => new Promise((resolve) => socket.timeout(8000).emit('routes', body, (err, res) => resolve(err ? { timeout: true } : res)));

describe('route explorer', () => {
  let gw;
  let url;
  let admin;
  let viewer;
  beforeAll(async () => {
    gw = createGateway({
      USE_MOCK: 'true', LIVE_LOOP: 'false', DB_PATH: ':memory:', MOCK_STEP_DELAY: '0.03', MOCK_MAX_STEPS: '100000',
      STATE_INTERVAL_MS: '50', PLAYLIST_PATH: path.join(tmp, 'none.json'), SUMMARY_PATH: path.join(tmp, 'summary.json'),
      LOG_LEVEL: 'silent', ...TEST_ENV,
    });
    const addr = await gw.listen(0, '127.0.0.1');
    url = `http://127.0.0.1:${addr.port}`;
    [admin, viewer] = await Promise.all(['ADMIN', 'VIEWER'].map((r) => sessionFor(gw, r)));
  });
  afterAll(async () => {
    await gw.close();
  });

  it('refuses when nothing runs and rejects invalid roads', async () => {
    const s = connect(url, viewer);
    try {
      await waitFor(() => s.connected);
      expect((await ask(s, { from: 'S2J0', to: 'J02N' })).code).toBe('not_running');
      for (const bad of [{ from: 'S2J0' }, { from: 'a b', to: 'J02N' }, { from: 'S2J0', to: 'S2J0' },
        { from: 'S2J0', to: 'J02N', vclass: 'tank' }, { from: 'S2J0', to: 'J02N', extra: 1 }]) {
        const r = await ask(s, bad);
        expect(r.ok, JSON.stringify(bad)).toBe(false);
        expect(r.code).toBe('invalid');
      }
    } finally {
      s.close();
    }
  });

  it('answers only the asking viewer, and one search at a time', async () => {
    const a = connect(url, viewer);
    const b = connect(url, viewer);
    const seenByB = [];
    b.onAny((name) => seenByB.push(name));
    try {
      await admin.post('/api/live/start').send({ scenario: 'normal', controller: 'fixed' }).expect(202);
      await waitFor(() => gw.live.lifecycle === 'RUNNING' && gw.live.latestState && a.connected && b.connected);
      const res = await ask(a, { from: 'S2J0', to: 'J02N', vclass: 'truck' });
      expect(res.ok).toBe(true);
      expect(res.origin).toBe('S2J0');
      expect(res.vclass).toBe('truck');
      expect(res.routes[0]).toMatchObject({ fastest: true, shortest: true });
      expect(res).not.toHaveProperty('qid'); // internal request id is not passed on
      expect(seenByB).not.toContain('routes');
      // a second search straight away is refused
      const quick = await Promise.all([ask(a, { from: 'S2J0', to: 'J02E' }), ask(a, { from: 'S2J0', to: 'J02W' })]);
      expect(quick.map((r) => r.code)).toContain('rate_limited');
      await admin.post('/api/live/stop').expect(200);
    } finally {
      a.close();
      b.close();
    }
  });
});
