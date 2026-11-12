// The history a new viewer receives keeps exactly what the dashboard's history charts read.
import { describe, expect, it } from 'vitest';
import { historyRow } from '../src/liveChannel.js';

const state = {
  type: 'state', run_id: 'live-1', t: 42,
  metrics: { avg_wait: 3.2, throughput: 10, n_vehicles: 25, avg_speed_kmh: 31 },
  tls: {
    J0: { phase: 'NS_GREEN', queues: { N: 1, S: 0, E: 2, W: 0 }, queues_est: { N: 1 }, signal: { N: 'G' },
      spat: { N: { signal: 'G', min_s: 3, max_s: 3, next_green_min_s: 40, next_green_max_s: 40 } } },
  },
  comm: { pdr: 0.93, sent: 400, delivered: 372 },
  nav: { avg_tt_nav: 61, avg_tt_non: 70, edge_load_cv: 0.4, reroutes: 12 },
  edge_speeds: { e1: 10.2, e2: 3.1 },
  eco: { fuel: 1.2 },
  incidents: { known: [], actual: [] },
};

describe('history rows', () => {
  it('keep time, metrics, phases, queues, delivery ratio and navigation averages', () => {
    const row = historyRow(state);
    expect(row).toEqual({
      type: 'state', run_id: 'live-1', t: 42, metrics: state.metrics,
      tls: { J0: { phase: 'NS_GREEN', queues: { N: 1, S: 0, E: 2, W: 0 } } },
      comm: { pdr: 0.93 },
      nav: { avg_tt_nav: 61, avg_tt_non: 70, edge_load_cv: 0.4 },
    });
    for (const gone of ['edge_speeds', 'eco', 'incidents', 'spat', 'queues_est', 'signal', 'reroutes', 'sent']) {
      expect(JSON.stringify(row)).not.toContain(`"${gone}"`);
    }
  });

  it('take the run id from the run when the state has none, and cope with missing parts', () => {
    expect(historyRow({ type: 'state', t: 1 }, 'live-9')).toEqual({ type: 'state', run_id: 'live-9', t: 1, metrics: undefined,
      tls: {}, comm: undefined, nav: undefined });
  });
});

describe('HTTP compression', () => {
  it('sends larger API answers gzip-compressed, and small ones as they are', async () => {
    const { default: fs } = await import('node:fs');
    const { default: os } = await import('node:os');
    const { default: path } = await import('node:path');
    const { default: request } = await import('supertest');
    const { createGateway } = await import('../src/app.js');
    const { sessionFor, TEST_ENV } = await import('./helpers.js');
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-gzip-'));
    const playlist = path.join(tmp, 'playlist.json');
    fs.writeFileSync(playlist, JSON.stringify(Array.from({ length: 40 }, (_, i) => ({ scenario: 'normal', controller: 'fixed', seed: i,
      label: `Seed ${i} of a long playlist used to make the answer larger than one kilobyte` }))));
    const gw = createGateway({ USE_MOCK: 'true', LIVE_LOOP: 'false', DB_PATH: ':memory:', PLAYLIST_PATH: playlist,
      SUMMARY_PATH: path.join(tmp, 'summary.json'), LOG_LEVEL: 'silent', ...TEST_ENV });
    try {
      const s = await sessionFor(gw, 'VIEWER');
      const big = await request(gw.app).get('/api/live/playlist').set('Cookie', s.cookie).set('Accept-Encoding', 'gzip');
      expect(big.status).toBe(200);
      expect(big.headers['content-encoding']).toBe('gzip');
      expect(big.body.playlist).toHaveLength(40);
      const small = await request(gw.app).get('/api/health').set('Accept-Encoding', 'gzip');
      expect(small.headers['content-encoding']).toBeUndefined();
    } finally {
      await gw.close();
    }
  });
});

describe('security headers', () => {
  it('limit live connections to this host in the CSP, and send HSTS only with secure cookies', async () => {
    const { default: request } = await import('supertest');
    const { createGateway } = await import('../src/app.js');
    const { TEST_ENV } = await import('./helpers.js');
    const make = (extra) => createGateway({ USE_MOCK: 'true', LIVE_LOOP: 'false', DB_PATH: ':memory:', LOG_LEVEL: 'silent', ...TEST_ENV, ...extra });
    const prod = make({ NODE_ENV: 'production' });
    const dev = make({});
    try {
      const res = await request(prod.app).get('/api/health').set('Host', 'traffic.example.org');
      const csp = res.headers['content-security-policy'];
      expect(csp).toContain("connect-src 'self' wss://traffic.example.org ws://traffic.example.org");
      expect(csp).not.toMatch(/connect-src[^;]* wss: /);
      expect(csp).toContain("frame-ancestors 'none'");
      expect(res.headers['strict-transport-security']).toContain('max-age=');
      expect(res.headers['x-powered-by']).toBeUndefined();
      const odd = await request(prod.app).get('/api/health').set('Host', 'x;script-src *');
      expect(odd.status).toBe(200);
      expect(odd.headers['content-security-policy']).toContain('wss://invalid.invalid');
      const local = await request(dev.app).get('/api/health');
      expect(local.headers['strict-transport-security']).toBeUndefined(); // plain HTTP in development
    } finally {
      await prod.close();
      await dev.close();
    }
  });
});

describe('traffic demand setting', () => {
  it('accepts 0.25 to 2, passes it to the simulation only when it differs from 1, and stores it with the run', async () => {
    const { buildSimArgs, RunEntrySchema } = await import('../src/protocol.js');
    const { openDatabase } = await import('../src/db.js');
    const ok = RunEntrySchema.parse({ scenario: 'normal', controller: 'fixed', demand: 0.5 });
    expect(ok.demand).toBe(0.5);
    expect(RunEntrySchema.parse({ scenario: 'normal', controller: 'fixed' }).demand).toBe(1);
    for (const bad of [0.1, 3, 'many']) expect(RunEntrySchema.safeParse({ scenario: 'normal', controller: 'fixed', demand: bad }).success).toBe(false);
    const cfg = { USE_MOCK: false, SIM_ENTRY: 'sim/main.py', SIM_STEP_DELAY: 0, SIM_GUI: false };
    const args = buildSimArgs(ok, 'r1', cfg);
    expect(args[args.indexOf('--demand') + 1]).toBe('0.5');
    expect(buildSimArgs({ ...ok, demand: 1 }, 'r1', cfg)).not.toContain('--demand');
    const db = openDatabase(':memory:');
    try {
      db.insertRun({ id: 'd1', scenario: 'normal', controller: 'fixed', nav: 'none', seed: 1, penetration: 1, loss: 0, relay: 'off', source: 'admin', demand: 1.5 });
      expect(db.getRun('d1').run.demand).toBe(1.5);
    } finally {
      db.close();
    }
  });
});
