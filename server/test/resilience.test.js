// Failure and recovery, hostile input, and database maintenance.
// Each test states the failure it provokes and what the gateway must do about it.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import request from 'supertest';
import { describe, expect, it } from 'vitest';
import { createGateway } from '../src/app.js';
import { openDatabase } from '../src/db.js';
import { connect, createUser, PASSWORD, sessionFor, signIn, TEST_ENV } from './helpers.js';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-res-'));
const base = {
  USE_MOCK: 'true', LIVE_LOOP: 'false', DB_PATH: ':memory:', MOCK_STEP_DELAY: '0.05', MOCK_MAX_STEPS: '200',
  STATE_INTERVAL_MS: '100', VIEWER_BROADCAST_MS: '100', CRASH_RETRY_MS: '100',
  PLAYLIST_PATH: path.join(tmp, 'none.json'), SUMMARY_PATH: path.join(tmp, 'summary.json'), LOG_LEVEL: 'silent', ...TEST_ENV,
};
const ENTRY = { scenario: 'normal', controller: 'fixed' };

const waitFor = async (pred, timeoutMs = 15_000, stepMs = 40) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await pred()) return true;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  throw new Error('waitFor timed out');
};

async function start(extra = {}) {
  const gw = createGateway({ ...base, ...extra });
  const addr = await gw.listen(0, '127.0.0.1');
  return { gw, url: `http://127.0.0.1:${addr.port}`, admin: await sessionFor(gw, 'ADMIN') };
}

/** A stand-in "simulation": a Python script that prints the given lines and exits with a code. */
function fakeSim(name, body) {
  const file = path.join(tmp, name);
  fs.writeFileSync(file, body);
  return file;
}

describe('simulation failures', () => {
  it('survives a simulation that is killed mid-run: FAILED, viewers told, run stored as error, restart works', async () => {
    const { gw, url, admin } = await start();
    const socket = connect(url, admin);
    const status = [];
    const playing = [];
    socket.on('status', (m) => status.push(m));
    socket.on('now_playing', (m) => playing.push(m));
    try {
      const res = await admin.post('/api/live/start').send(ENTRY).expect(202);
      await waitFor(() => gw.live.lifecycle === 'RUNNING' && gw.live.states.length > 3);
      gw.live.proc.child.kill(); // the worker dies without a word (not a requested stop)
      await waitFor(() => gw.live.lifecycle === 'FAILED');
      expect(gw.live.proc).toBeNull();
      expect(gw.db.getRun(res.body.run_id).run.status).toBe('error');
      await waitFor(() => status.some((s) => s.run_id === res.body.run_id && s.status === 'error'));
      await waitFor(() => playing.some((p) => p.lifecycle === 'FAILED' && p.running === false));
      expect((await request(gw.app).get('/api/health').expect(200)).body).toMatchObject({ live_running: false, lifecycle: 'FAILED' });
      // No automatic restart without a playlist; an explicit start recovers
      await new Promise((r) => setTimeout(r, 400));
      expect(gw.db.listRuns(10)).toHaveLength(1);
      await admin.post('/api/live/start').send(ENTRY).expect(202);
      await waitFor(() => gw.live.lifecycle === 'RUNNING');
    } finally {
      socket.close();
      await gw.close();
    }
  });

  it('reports a simulation that cannot be started (missing interpreter) instead of claiming it runs', async () => {
    const { gw, admin } = await start({ PYTHON_BIN: path.join(tmp, 'no-such-python') });
    try {
      const res = await admin.post('/api/live/start').send(ENTRY).expect(202); // accepted, then fails at once
      await waitFor(() => gw.live.lifecycle === 'FAILED');
      expect(gw.db.getRun(res.body.run_id).run.status).toBe('error');
      expect((await admin.get('/api/live/status').expect(200)).body).toMatchObject({ running: false, lifecycle: 'FAILED' });
      expect((await admin.get('/api/system').expect(200)).body.simulation).toMatchObject({ lifecycle: 'FAILED', failures: 1 });
    } finally {
      await gw.close();
    }
  });

  it('ignores malformed output lines, keeps the valid ones, and counts the bad ones', async () => {
    const script = fakeSim('garbage.py', [
      'import json, sys, time',
      'print("not json at all")',
      'print("{broken json")',
      'print(json.dumps({"type": "nonsense", "x": 1}))',
      'print(json.dumps([1, 2, 3]))',
      'print("x" * 200000)',
      'print(json.dumps({"type": "status", "run_id": "r", "status": "started", "message": "ok"}))',
      'for t in range(1, 4):',
      '    print(json.dumps({"type": "state", "run_id": "r", "t": t, "tls": {}, "ev": [], "metrics": {"n_vehicles": t}}))',
      '    sys.stdout.flush(); time.sleep(0.15)',
      'print(json.dumps({"type": "event", "run_id": "r", "t": 3, "event": "alert_sent", "tls_id": "J1"}))',
      'print(json.dumps({"type": "result", "run_id": "r", "metrics": {"avg_waiting_time": 1.5}}))',
      'print(json.dumps({"type": "status", "run_id": "r", "status": "finished", "message": "done"}))',
    ].join('\n'));
    const { gw, admin } = await start({ MOCK_ENTRY: script });
    try {
      const res = await admin.post('/api/live/start').send(ENTRY).expect(202);
      await waitFor(() => gw.db.getRun(res.body.run_id).run.status === 'finished');
      expect(gw.live.stats.bad_lines).toBe(5);
      expect(gw.live.lifecycle).toBe('STOPPED');
      const stored = gw.db.getRun(res.body.run_id);
      expect(stored.result.metrics).toEqual({ avg_waiting_time: 1.5 });
      // the simulation's own event, plus the platform's run events (event centre and bell)
      expect(stored.events.filter((e) => !e.kind.startsWith('sim_'))).toEqual([{ t: 3, kind: 'alert_sent', tls: 'J1', detail: '' }]);
      expect(stored.events.map((e) => e.kind).filter((k) => k.startsWith('sim_'))).toContain('sim_finished');
      expect(gw.live.latestState.metrics.n_vehicles).toBe(3);
    } finally {
      await gw.close();
    }
  });

  it('treats an exit without a result as a failure even when the exit code is 0', async () => {
    const script = fakeSim('silent.py', 'print("{\\"type\\": \\"status\\", \\"run_id\\": \\"r\\", \\"status\\": \\"started\\"}")\n');
    const { gw, admin } = await start({ MOCK_ENTRY: script });
    try {
      const res = await admin.post('/api/live/start').send(ENTRY).expect(202);
      await waitFor(() => gw.live.lifecycle === 'FAILED');
      expect(gw.db.getRun(res.body.run_id).run.status).toBe('error');
    } finally {
      await gw.close();
    }
  });

  it('stops the simulation and leaves no process behind when the gateway shuts down mid-run', async () => {
    const { gw, admin } = await start();
    const res = await admin.post('/api/live/start').send(ENTRY).expect(202);
    await waitFor(() => gw.live.lifecycle === 'RUNNING');
    const child = gw.live.proc.child;
    await gw.close();
    expect(child.exitCode !== null || child.signalCode !== null || child.killed).toBe(true);
    await gw.close(); // closing twice is harmless
    expect(res.body.run_id).toMatch(/^live-/);
  });
});

describe('gateway restart and reconnection', () => {
  it('keeps accounts and sessions across a restart and marks the interrupted run', async () => {
    const dbPath = path.join(tmp, 'restart.db');
    const first = createGateway({ ...base, DB_PATH: dbPath });
    const user = await createUser(first, 'RESEARCHER');
    const session = await signIn(first, user.email);
    // The process dies during a run: no clean shutdown, the run stays "running" in the file
    first.db.insertRun({ id: 'r-live', scenario: 'normal', controller: 'fixed', nav: 'none', seed: 1, penetration: 1, loss: 0,
      relay: 'off', source: 'admin', started_by: user.id });
    first.auth.close();
    first.db.close();

    const second = createGateway({ ...base, DB_PATH: dbPath });
    try {
      // Same cookie, new process: still signed in, with the same role
      const me = await request(second.app).get('/api/auth/me').set('Cookie', session.cookie).expect(200);
      expect(me.body.user).toMatchObject({ email: user.email, role: 'RESEARCHER' });
      expect(second.db.getRun('r-live').run.status).toBe('interrupted');
      expect(second.live.lifecycle).toBe('STOPPED');
    } finally {
      await second.close();
    }
  });

  it('gives a reconnecting viewer the current snapshot of the run', async () => {
    const { gw, url, admin } = await start();
    try {
      await admin.post('/api/live/start').send(ENTRY).expect(202);
      const a = connect(url, admin);
      const firstInit = await new Promise((resolve) => a.on('init', resolve));
      await waitFor(() => gw.live.states.length > 8);
      a.close();
      const b = connect(url, admin);
      const init = await new Promise((resolve) => b.on('init', resolve));
      b.close();
      expect(init.now_playing.current.run_id).toBe(firstInit.now_playing.current.run_id);
      expect(init.states.length).toBeGreaterThan(8);
      expect(init.network).not.toBeNull();
      await waitFor(() => gw.live.viewers() === 0);
    } finally {
      await gw.close();
    }
  });
});

describe('database unavailable', () => {
  it('answers 500/503 with a plain message (no stack trace) and keeps serving after', async () => {
    const { gw, admin } = await start();
    try {
      const real = gw.db.raw;
      const broken = () => { throw new Error('SQLITE_IOERR: disk I/O error at /secret/path/gateway.db'); };
      const prepared = real.prepare;
      real.prepare = broken; // every new statement now fails
      const sys = await admin.get('/api/system');
      expect(sys.status).toBe(503);
      expect(sys.body.database).toEqual({ ok: false, error: 'Database is not available' });
      expect(JSON.stringify(sys.body)).not.toMatch(/secret|SQLITE|at /);
      real.prepare = prepared;
      await admin.get('/api/system').expect(200); // back to normal without a restart

      // A failure inside request handling: generic 500, nothing leaked
      const getSession = gw.db.getSession;
      gw.db.getSession = broken;
      const res = await admin.get('/api/options');
      expect(res.status).toBe(500);
      expect(res.body).toEqual({ error: 'Internal server error', code: 'server_error' });
      gw.db.getSession = getSession;
      await admin.get('/api/options').expect(200);
    } finally {
      await gw.close();
    }
  });
});

describe('hostile input', () => {
  it('rejects oversized, malformed and unexpected requests without side effects', async () => {
    const { gw, admin } = await start();
    try {
      await admin.post('/api/users').set('content-type', 'application/json').send(`{"name":"${'a'.repeat(20_000)}"}`).expect(413);
      await admin.post('/api/live/start').set('content-type', 'application/json').send('{"scenario": ').expect(400);
      await admin.post('/api/live/start').send({ scenario: 'normal', controller: 'fixed', seed: -1 }).expect(400);
      await admin.post('/api/live/start').send({ scenario: 'normal', controller: 'fixed', network: '../../etc/passwd' }).expect(400);
      await admin.post('/api/live/start').send({ scenario: 'normal; rm -rf /', controller: 'fixed' }).expect(400);
      await admin.post('/api/live/start').send({ ...ENTRY, __proto__: { admin: true }, constructor: 'x' }).expect(400);
      expect(gw.live.proc).toBeNull(); // nothing was started
      // Unknown routes and methods
      await admin.get('/api/users/1/password').expect(404);
      await admin.del('/api/audit').expect(404);
      await admin.get(`/api/runs/${encodeURIComponent("x' OR '1'='1")}`).expect(404);
      await admin.get('/api/runs?limit=999999&offset=-5').expect(200).then((r) => expect(r.body).toMatchObject({ limit: 500, offset: 0 }));
      await admin.get('/api/audit?action=%27%3B%20DROP%20TABLE%20users%3B--').expect(200); // ignored filter, table intact
      expect(gw.db.countUsers()).toBe(1);
    } finally {
      await gw.close();
    }
  });

  it('stores script-like text as plain data and serves it as JSON, never as HTML', async () => {
    const { gw, admin } = await start();
    try {
      const xss = '<img src=x onerror=alert(1)><script>alert(2)</script>';
      const made = await admin.post('/api/users').send({ email: 'x@test.local', name: xss, password: PASSWORD }).expect(201);
      expect(made.body.user.name).toBe(xss);
      const list = await admin.get('/api/users').expect(200);
      expect(list.headers['content-type']).toMatch(/application\/json/);
      expect(list.headers['x-content-type-options']).toBe('nosniff');
      expect(list.body.users.find((u) => u.email === 'x@test.local').name).toBe(xss);
      const audit = await admin.get('/api/audit?action=user_create').expect(200);
      expect(audit.headers['content-type']).toMatch(/application\/json/);
    } finally {
      await gw.close();
    }
  });

  it('never serves files outside the built dashboard', async () => {
    const { gw } = await start();
    try {
      for (const p of ['/../server/.env', '/..%2f..%2fserver%2f.env', '/%2e%2e/%2e%2e/package.json', '/assets/../../server/data/gateway.db']) {
        const res = await request(gw.app).get(p);
        expect(res.text ?? '', p).not.toMatch(/PYTHON_BIN|ADMIN_PASSWORD|"dependencies"|SQLite format/);
      }
    } finally {
      await gw.close();
    }
  });

  it('ignores socket events a viewer has no right to and malformed payloads', async () => {
    const { gw, url, admin } = await start();
    const viewer = await sessionFor(gw, 'VIEWER');
    const socket = connect(url, viewer);
    const ask = (name, body) => new Promise((resolve) => socket.emit(name, body, resolve));
    try {
      await new Promise((resolve) => socket.on('init', resolve));
      await admin.post('/api/live/start').send(ENTRY).expect(202);
      await waitFor(() => gw.live.network !== null && gw.live.driver.public().available);
      expect((await ask('driver:cmd', { action: 'stop' })).ok).toBe(false); // not the driver
      expect((await ask('driver:release')).ok).toBe(false);
      expect((await ask('driver:resume', { $ne: null })).ok).toBe(false);
      expect((await ask('driver:claim', { from: "'; DROP TABLE runs;--", to: 'x' })).ok).toBe(false);
      expect((await ask('driver:claim', 'not an object')).ok).toBe(false);
      for (const name of ['live:start', 'admin:stop', 'state', 'now_playing', 'init']) socket.emit(name, { scenario: 'congested' });
      socket.emit('follow', { toString: 'x' });
      socket.emit('follow', 'a'.repeat(5000));
      await new Promise((r) => setTimeout(r, 300));
      expect(socket.connected).toBe(true);
      expect(gw.live.current.entry.scenario).toBe('normal'); // nothing a viewer sent changed the run
      expect(gw.live.driver.public().busy).toBe(false);
    } finally {
      socket.close();
      await gw.close();
    }
  });

  it('holds up under concurrent sign-ins and reads', async () => {
    const { gw, admin } = await start();
    try {
      const users = await Promise.all(Array.from({ length: 25 }, () => createUser(gw, 'VIEWER')));
      const sessions = await Promise.all(users.map((u) => signIn(gw, u.email)));
      const results = await Promise.all(sessions.flatMap((s) => [s.get('/api/options'), s.get('/api/runs'), s.get('/api/auth/me')]));
      expect(results.every((r) => r.status === 200)).toBe(true);
      expect(new Set(sessions.map((s) => s.cookie)).size).toBe(25);
      // Two administrators create the same account at once: exactly one succeeds
      const other = await sessionFor(gw, 'ADMIN');
      const body = { email: 'race@test.local', name: 'Race', password: PASSWORD };
      const [a, b] = await Promise.all([admin.post('/api/users').send(body), other.post('/api/users').send(body)]);
      expect([a.status, b.status].sort()).toEqual([201, 409]);
    } finally {
      await gw.close();
    }
  });
});

describe('database write failures during a run', () => {
  it('keeps streaming to viewers, counts the errors and does not crash', async () => {
    const { gw, url, admin } = await start({ MOCK_MAX_STEPS: '40' });
    const socket = connect(url, admin);
    const got = { state: 0, result: null };
    socket.on('state', () => { got.state += 1; });
    socket.on('result', (m) => { got.result = m; });
    const fail = () => { throw new Error('SQLITE_FULL: database or disk is full'); };
    const saved = { sample: gw.db.saveSample, event: gw.db.saveEvent, result: gw.db.saveResult };
    try {
      await new Promise((resolve) => socket.on('init', resolve));
      Object.assign(gw.db, { saveSample: fail, saveEvent: fail, saveResult: fail });
      await admin.post('/api/live/start').send({ scenario: 'emergency', controller: 'adaptive_ev_vanet' }).expect(202);
      await waitFor(() => got.result !== null);
      expect(got.state).toBeGreaterThan(5); // viewers were served throughout
      expect(gw.live.stats.persist_errors).toBeGreaterThan(5);
      expect((await admin.get('/api/metrics').expect(200)).body.live.persist_errors).toBe(gw.live.stats.persist_errors);
      // Storage works again: the next run is stored normally
      Object.assign(gw.db, { saveSample: saved.sample, saveEvent: saved.event, saveResult: saved.result });
      const res = await admin.post('/api/live/start').send(ENTRY).expect(202);
      await waitFor(() => gw.db.getRun(res.body.run_id).run.status === 'finished');
      expect(gw.db.getRun(res.body.run_id).result).not.toBeNull();
    } finally {
      socket.close();
      await gw.close();
    }
  });
});

describe('database maintenance', () => {
  it('deletes old state samples but keeps the runs, results and events', () => {
    const db = openDatabase(':memory:');
    try {
      db.insertRun({ id: 'r1', scenario: 'normal', controller: 'fixed', nav: 'none', seed: 1, penetration: 1, loss: 0, relay: 'off', source: 'admin' });
      for (let t = 0; t < 6; t += 1) db.saveSample('r1', { t });
      db.saveEvent('r1', { t: 1, kind: 'alert_sent', tls: 'J0', detail: '' });
      db.saveResult('r1', { avg_waiting_time: 2 });
      db.raw.prepare("UPDATE state_samples SET created_at = '2020-01-01T00:00:00.000Z' WHERE t < 4").run();
      expect(db.pruneSamples('2021-01-01T00:00:00.000Z')).toBe(4);
      expect(db.countSamples()).toBe(2);
      const run = db.getRun('r1');
      expect(run.result.metrics.avg_waiting_time).toBe(2);
      expect(run.events).toHaveLength(1);
    } finally {
      db.close();
    }
  });

  it('applies the retention at start-up', async () => {
    const dbPath = path.join(tmp, 'retention.db');
    const seed = openDatabase(dbPath);
    seed.insertRun({ id: 'old', scenario: 'normal', controller: 'fixed', nav: 'none', seed: 1, penetration: 1, loss: 0, relay: 'off', source: 'admin' });
    seed.saveSample('old', { t: 1 });
    seed.saveSample('old', { t: 2 });
    seed.raw.prepare("UPDATE state_samples SET created_at = '2020-01-01T00:00:00.000Z' WHERE t = 1").run();
    seed.raw.prepare("UPDATE runs SET status = 'finished'").run();
    seed.close();
    const gw = createGateway({ ...base, DB_PATH: dbPath, SAMPLE_RETENTION_DAYS: '14' });
    try {
      expect(gw.db.countSamples()).toBe(1);
    } finally {
      await gw.close();
    }
  });

  it('backs up a database in use and the copy restores with everything in it', async () => {
    const dbPath = path.join(tmp, 'live.db');
    const gw = createGateway({ ...base, DB_PATH: dbPath });
    const backupFile = path.join(tmp, 'backup.db');
    try {
      const user = await createUser(gw, 'RESEARCHER');
      gw.db.insertRun({ id: 'kept', scenario: 'normal', controller: 'fixed', nav: 'none', seed: 7, penetration: 1, loss: 0, relay: 'off', source: 'admin' });
      gw.db.saveResult('kept', { avg_waiting_time: 3.5 });
      gw.db.audit({ action: 'login', target: user.email });
      await gw.db.backup(backupFile);
      gw.db.deleteUser(user.id); // changes after the backup are not in it

      const restored = createGateway({ ...base, DB_PATH: backupFile });
      try {
        expect(restored.db.getUserByEmail(user.email)).toMatchObject({ role: 'RESEARCHER', status: 'active' });
        expect(restored.db.getRun('kept').result.metrics.avg_waiting_time).toBe(3.5);
        expect(restored.db.raw.prepare('PRAGMA integrity_check').get().integrity_check).toBe('ok');
        expect(restored.db.raw.prepare('SELECT version FROM schema_migrations ORDER BY version').all().map((r) => r.version)).toEqual([1, 2, 3, 4, 5]);
        await signIn(restored, user.email); // the password still works
      } finally {
        await restored.close();
      }
    } finally {
      await gw.close();
    }
  });

  it('uses indexes for the frequent lookups', () => {
    const db = openDatabase(':memory:');
    try {
      const plan = (sql) => db.raw.prepare(`EXPLAIN QUERY PLAN ${sql}`).all().map((r) => r.detail).join(' | ');
      expect(plan("SELECT * FROM sessions WHERE token_hash = 'x'")).toMatch(/USING (COVERING )?INDEX/);
      expect(plan("SELECT * FROM users WHERE email = 'x'")).toMatch(/USING (COVERING )?INDEX/);
      expect(plan("SELECT * FROM events WHERE run_id = 'x' ORDER BY id LIMIT 10")).toMatch(/USING (COVERING )?INDEX/);
      expect(plan("SELECT * FROM state_samples WHERE run_id = 'x' ORDER BY t LIMIT 10")).toMatch(/USING (COVERING )?INDEX/);
      expect(plan("SELECT * FROM audit_logs WHERE action = 'login' ORDER BY id DESC LIMIT 10")).toMatch(/USING (COVERING )?INDEX/);
      expect(plan('SELECT * FROM runs ORDER BY started_at DESC LIMIT 10')).toMatch(/USING (COVERING )?INDEX/);
    } finally {
      db.close();
    }
  });
});
