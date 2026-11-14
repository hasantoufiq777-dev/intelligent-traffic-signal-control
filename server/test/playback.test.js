// Live playback control: pause, resume, speed and restart of the shared run. Only an administrator,
// or the researcher who started the run; viewers see the state the simulation confirms.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createGateway } from '../src/app.js';
import { connect, sessionFor, TEST_ENV } from './helpers.js';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-playback-'));
const waitFor = async (pred, timeoutMs = 15_000, stepMs = 25) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await pred()) return true;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  throw new Error('waitFor timed out');
};
const ENTRY = { scenario: 'normal', controller: 'fixed' };

describe('live playback control', () => {
  let gw;
  let url;
  let admin;
  let viewer;
  let researcher;
  let other;
  beforeAll(async () => {
    gw = createGateway({
      USE_MOCK: 'true', LIVE_LOOP: 'false', DB_PATH: ':memory:', MOCK_STEP_DELAY: '0.05', MOCK_MAX_STEPS: '100000',
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

  it('refuses playback when nothing runs, and to viewers', async () => {
    for (const p of ['pause', 'resume', 'restart']) {
      expect((await admin.post(`/api/live/${p}`)).body.code).toBe('not_running');
    }
    expect((await admin.post('/api/live/speed').send({ speed: 2 })).status).toBe(409);
    for (const p of ['pause', 'resume', 'restart']) expect((await viewer.post(`/api/live/${p}`)).status).toBe(403);
    expect((await viewer.post('/api/live/speed').send({ speed: 2 })).status).toBe(403);
  });

  it('pauses and resumes the simulation; viewers see the confirmed state and time stands still', async () => {
    const socket = connect(url, viewer);
    const playbacks = [];
    socket.on('now_playing', (np) => playbacks.push(np.playback));
    try {
      await admin.post('/api/live/start').send(ENTRY).expect(202);
      await waitFor(() => gw.live.lifecycle === 'RUNNING' && gw.live.latestState?.t > 3);
      expect(gw.live.nowPlaying().playback).toEqual({ paused: false, speed: 1 });

      expect((await admin.post('/api/live/pause')).status).toBe(202);
      await waitFor(() => gw.live.playback.paused === true); // confirmed by the simulation
      await waitFor(() => playbacks.some((p) => p?.paused === true));
      const tPaused = gw.live.latestState.t;
      await new Promise((r) => setTimeout(r, 600)); // ~12 simulated seconds at this pace if it were running
      expect(gw.live.latestState.t).toBeLessThanOrEqual(tPaused + 1);
      expect(gw.live.lifecycle).toBe('RUNNING'); // paused is a playback state, the run is still live

      await admin.post('/api/live/resume').expect(202);
      await waitFor(() => gw.live.playback.paused === false);
      await waitFor(() => gw.live.latestState.t > tPaused + 3);
    } finally {
      socket.close();
    }
  });

  it('changes the speed only to the allowed values', async () => {
    for (const bad of [0, 3, -1, 'fast', 100]) {
      const r = await admin.post('/api/live/speed').send({ speed: bad });
      expect(r.status).toBe(400);
    }
    expect((await admin.post('/api/live/speed').send({ speed: 1, extra: true })).status).toBe(400);
    await admin.post('/api/live/speed').send({ speed: 4 }).expect(202);
    await waitFor(() => gw.live.playback.speed === 4);
    await admin.post('/api/live/speed').send({ speed: 0.5 }).expect(202);
    await waitFor(() => gw.live.playback.speed === 0.5);
  });

  it('restarts the run from the beginning with the same settings, at normal speed', async () => {
    const before = gw.live.current.run_id;
    const r = await admin.post('/api/live/restart').expect(202);
    expect(r.body.run_id).not.toBe(before);
    expect(r.body.entry.scenario).toBe('normal');
    expect(gw.live.playback).toEqual({ paused: false, speed: 1 });
    // RUNNING is set by the simulation's "started" status, which can come before its first state
    await waitFor(() => gw.live.lifecycle === 'RUNNING' && gw.live.latestState);
    expect(gw.live.latestState.t).toBeLessThan(20);
  });

  it('lets a researcher control only the runs they started', async () => {
    // the administrator's run: refused for a researcher
    expect((await researcher.post('/api/live/pause')).status).toBe(403);
    expect((await researcher.post('/api/live/restart')).status).toBe(403);
    // a playlist-free stop, then the researcher's own run
    await admin.post('/api/live/stop').expect(200);
    await researcher.post('/api/live/start').send(ENTRY).expect(202);
    await waitFor(() => gw.live.lifecycle === 'RUNNING');
    await researcher.post('/api/live/pause').expect(202);
    await waitFor(() => gw.live.playback.paused === true);
    expect((await other.post('/api/live/resume')).status).toBe(403); // another researcher
    await researcher.post('/api/live/resume').expect(202);
    await waitFor(() => gw.live.playback.paused === false);
    const audit = (await admin.get('/api/audit?limit=100')).body.entries.map((e) => e.action);
    expect(audit).toEqual(expect.arrayContaining(['sim_pause', 'sim_resume', 'sim_speed', 'sim_restart']));
    await admin.post('/api/live/stop').expect(200);
  });

  it('stops a paused run promptly', async () => {
    await admin.post('/api/live/start').send(ENTRY).expect(202);
    await waitFor(() => gw.live.lifecycle === 'RUNNING');
    await admin.post('/api/live/pause').expect(202);
    await waitFor(() => gw.live.playback.paused === true);
    const t0 = Date.now();
    await admin.post('/api/live/stop').expect(200);
    expect(gw.live.proc).toBeNull();
    expect(Date.now() - t0).toBeLessThan(5_000);
  });
});

describe('system events', () => {
  it('records run start, end, failure streaks and the connection limit as events for the event centre', async () => {
    const tmp2 = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-sysev-'));
    const gw2 = createGateway({
      USE_MOCK: 'true', LIVE_LOOP: 'false', DB_PATH: ':memory:', MOCK_STEP_DELAY: '0.02', MOCK_MAX_STEPS: '30',
      STATE_INTERVAL_MS: '50', MAX_CONNECTIONS: '1', PLAYLIST_PATH: path.join(tmp2, 'none.json'),
      SUMMARY_PATH: path.join(tmp2, 'summary.json'), LOG_LEVEL: 'silent', ...TEST_ENV,
    });
    const addr = await gw2.listen(0, '127.0.0.1');
    const u = `http://127.0.0.1:${addr.port}`;
    const adm = await sessionFor(gw2, 'ADMIN');
    const got = [];
    const first = connect(u, adm);
    first.on('event', (e) => got.push(e));
    try {
      await waitFor(() => first.connected);
      const second = connect(u, adm); // over the limit of 1
      await new Promise((r) => second.on('connect_error', r));
      second.close();
      await adm.post('/api/live/start').send(ENTRY).expect(202);
      await waitFor(() => got.some((e) => e.kind === 'sim_finished'), 15_000);
      const kinds = got.map((e) => e.kind);
      expect(kinds).toEqual(expect.arrayContaining(['sim_started', 'sim_finished']));
      expect(got.find((e) => e.kind === 'sim_started').detail).toContain('normal with fixed');
      expect(gw2.live.events.map((e) => e.kind)).toContain('sim_finished'); // in the history for late joiners
      // the capacity warning went to the event list too (before the run, so it is not in this run's events)
      expect(kinds.indexOf('capacity_reached')).toBeGreaterThanOrEqual(0);
    } finally {
      first.close();
      await gw2.close();
    }
  });
});
