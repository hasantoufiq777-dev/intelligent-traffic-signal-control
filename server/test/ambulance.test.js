// Ambulance on demand: an administrator (or the researcher who started the run) sends an ambulance into
// the running simulation at any moment. The simulation reports it with events (dispatched, priority at
// the junction, arrived) and adds the trips to the run's stored result.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createGateway } from '../src/app.js';
import { connect, sessionFor, TEST_ENV } from './helpers.js';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-ambulance-'));
const waitFor = async (pred, timeoutMs = 15_000, stepMs = 25) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await pred()) return true;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  throw new Error('waitFor timed out');
};

describe('ambulance on demand', () => {
  let gw;
  let url;
  let admin;
  let viewer;
  let researcher;
  let other;
  beforeAll(async () => {
    gw = createGateway({
      USE_MOCK: 'true', LIVE_LOOP: 'false', DB_PATH: ':memory:', MOCK_STEP_DELAY: '0.03', MOCK_MAX_STEPS: '100000',
      STATE_INTERVAL_MS: '50', VIEWER_BROADCAST_MS: '100', PLAYLIST_PATH: path.join(tmp, 'none.json'),
      SUMMARY_PATH: path.join(tmp, 'summary.json'), LOG_LEVEL: 'silent', ...TEST_ENV,
    });
    const addr = await gw.listen(0, '127.0.0.1');
    url = `http://127.0.0.1:${addr.port}`;
    [admin, viewer, researcher, other] = await Promise.all(['ADMIN', 'VIEWER', 'RESEARCHER', 'RESEARCHER'].map((r) => sessionFor(gw, r)));
  });
  afterAll(async () => {
    await gw.close();
  });

  it('is refused when nothing runs, to viewers, and with invalid roads', async () => {
    expect((await admin.post('/api/live/ambulance')).status).toBe(409);
    expect((await admin.post('/api/live/ambulance')).body.code).toBe('not_running');
    expect((await viewer.post('/api/live/ambulance')).status).toBe(403);
    for (const bad of [{ from: 'S2J0' }, { from: 'S2J0', to: 'S2J0' }, { from: 'a b', to: 'J02N' }, { from: '<x>', to: 'J02N' },
      { from: 'S2J0', to: 'J02N', extra: 1 }]) {
      const r = await admin.post('/api/live/ambulance').send(bad);
      expect(r.status, JSON.stringify(bad)).toBe(400);
      expect(r.body.code).toBe('invalid');
    }
  });

  it('sends an ambulance into a running fixed-time run: priority switched on, green given, trip stored', async () => {
    const socket = connect(url, viewer);
    const events = [];
    socket.on('event', (e) => events.push(e));
    try {
      await admin.post('/api/live/start').send({ scenario: 'normal', controller: 'fixed' }).expect(202);
      await waitFor(() => gw.live.lifecycle === 'RUNNING' && gw.live.latestState?.t > 3);
      const runId = gw.live.current.run_id;

      const r = await admin.post('/api/live/ambulance').expect(202);
      expect(r.body).toEqual({ requested: 'ambulance' });
      await waitFor(() => events.some((e) => e.kind === 'ambulance_arrived'));
      const kinds = events.filter((e) => e.run_id === runId).map((e) => e.kind);
      expect(kinds.indexOf('ambulance_dispatched')).toBeLessThan(kinds.indexOf('preempt_start'));
      expect(kinds.indexOf('preempt_start')).toBeLessThan(kinds.indexOf('preempt_end'));
      expect(kinds.indexOf('preempt_end')).toBeLessThan(kinds.indexOf('ambulance_arrived'));
      const sent = events.find((e) => e.kind === 'ambulance_dispatched');
      expect(sent.detail).toContain('ambulance priority switched on at J0'); // fixed time has none of its own
      // the ambulance is in the live state while it approaches (map and Emergency page)
      expect(gw.live.states.length).toBeGreaterThan(0);

      // a chosen route
      await admin.post('/api/live/ambulance').send({ from: 'E2J0', to: 'J02W' }).expect(202);
      await waitFor(() => events.filter((e) => e.kind === 'ambulance_arrived').length === 2);
      expect(events.filter((e) => e.kind === 'ambulance_dispatched')[1].detail).toContain('from E2J0 to J02W');

      await admin.post('/api/live/stop').expect(200);
      expect(gw.db.getRun(runId).events.map((e) => e.kind))
        .toEqual(expect.arrayContaining(['ambulance_dispatched', 'preempt_start', 'ambulance_arrived']));
      const audit = (await admin.get('/api/audit?limit=50')).body.entries;
      expect(audit.filter((e) => e.action === 'sim_ambulance').map((e) => e.detail))
        .toEqual(expect.arrayContaining(['longest crossing', 'E2J0 -> J02W']));
    } finally {
      socket.close();
    }
  });

  it('stores the dispatched trips in the result of a run that finishes', async () => {
    const tmp2 = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-ambulance-short-'));
    const gw2 = createGateway({
      USE_MOCK: 'true', LIVE_LOOP: 'false', DB_PATH: ':memory:', MOCK_STEP_DELAY: '0.05', MOCK_MAX_STEPS: '60',
      STATE_INTERVAL_MS: '50', PLAYLIST_PATH: path.join(tmp2, 'none.json'), SUMMARY_PATH: path.join(tmp2, 'summary.json'),
      LOG_LEVEL: 'silent', ...TEST_ENV,
    });
    await gw2.listen(0, '127.0.0.1');
    const adm = await sessionFor(gw2, 'ADMIN');
    try {
      await adm.post('/api/live/start').send({ scenario: 'normal', controller: 'adaptive' }).expect(202);
      await waitFor(() => gw2.live.lifecycle === 'RUNNING' && gw2.live.latestState?.t > 2);
      const runId = gw2.live.current.run_id;
      await adm.post('/api/live/ambulance').expect(202);
      await waitFor(() => gw2.db.getRun(runId)?.result, 20_000);
      const m = gw2.db.getRun(runId).result.metrics;
      expect(m.dispatched_ambulances).toBe(1);
      expect(m.dispatched_arrived).toBe(1);
      expect(m.dispatched_avg_travel_time).toBeGreaterThan(0);
      expect(m.dispatched_priority_added).toEqual(['J0']);
      expect(m.dispatched_trips[0]).toMatchObject({ id: 'amb_dispatch_1', junctions: ['J0'], priority_at: ['J0'] });
    } finally {
      await gw2.close();
    }
  });

  it('uses the junctions\' own priority in an ambulance-priority run, and limits ambulances on the road', async () => {
    const events = [];
    const socket = connect(url, viewer);
    socket.on('event', (e) => events.push(e));
    try {
      await admin.post('/api/live/start').send({ scenario: 'normal', controller: 'adaptive_ev' }).expect(202);
      await waitFor(() => gw.live.lifecycle === 'RUNNING' && gw.live.latestState?.t > 2);
      await admin.post('/api/live/pause').expect(202); // sent while paused: applied when the run resumes
      await waitFor(() => gw.live.playback.paused === true);
      for (let i = 0; i < 4; i += 1) await admin.post('/api/live/ambulance').expect(202);
      await admin.post('/api/live/resume').expect(202);
      await waitFor(() => events.filter((e) => e.kind.startsWith('ambulance_')).length >= 4);
      const sent = events.filter((e) => e.kind === 'ambulance_dispatched');
      expect(sent).toHaveLength(3);
      expect(sent[0].detail).toContain("the junctions' own ambulance priority");
      expect(events.find((e) => e.kind === 'ambulance_rejected').detail).toContain('3 dispatched ambulances');
      await admin.post('/api/live/stop').expect(200);
    } finally {
      socket.close();
    }
  });

  it('lets a researcher send one only into a run they started', async () => {
    await admin.post('/api/live/start').send({ scenario: 'normal', controller: 'fixed' }).expect(202);
    await waitFor(() => gw.live.lifecycle === 'RUNNING');
    expect((await researcher.post('/api/live/ambulance')).status).toBe(403);
    await admin.post('/api/live/stop').expect(200);
    await researcher.post('/api/live/start').send({ scenario: 'normal', controller: 'fixed' }).expect(202);
    await waitFor(() => gw.live.lifecycle === 'RUNNING');
    expect((await other.post('/api/live/ambulance')).status).toBe(403);
    await researcher.post('/api/live/ambulance').expect(202);
    await admin.post('/api/live/stop').expect(200);
  });
});
