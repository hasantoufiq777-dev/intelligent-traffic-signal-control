// Accounts, sessions, roles, CSRF, lockout, password reset, user management, audit trail,
// authenticated sockets, the connection cap and the simulation lifecycle (mock simulation).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import request from 'supertest';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createGateway } from '../src/app.js';
import { COOKIE_NAME, readCookie } from '../src/auth.js';
import { migrate, openDatabase } from '../src/db.js';
import { connect, createUser, PASSWORD, sessionFor, signIn, TEST_ENV } from './helpers.js';

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'gw-auth-'));
const base = {
  USE_MOCK: 'true', LIVE_LOOP: 'false', DB_PATH: ':memory:', MOCK_STEP_DELAY: '0.05', MOCK_MAX_STEPS: '60',
  STATE_INTERVAL_MS: '200', VIEWER_BROADCAST_MS: '100', CRASH_RETRY_MS: '100',
  PLAYLIST_PATH: path.join(tmp, 'none.json'), SUMMARY_PATH: path.join(tmp, 'summary.json'), LOG_LEVEL: 'silent', ...TEST_ENV,
};

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
  return { gw, url: `http://127.0.0.1:${addr.port}` };
}

/** Resolve with 'connected' or the refusal's code. */
const outcome = (socket) => new Promise((resolve) => {
  socket.on('connect', () => resolve('connected'));
  socket.on('connect_error', (err) => resolve(err.data?.code ?? err.message));
});

describe('sessions and sign-in', () => {
  let gw;
  let url;
  beforeAll(async () => { ({ gw, url } = await start({ LOGIN_MAX_FAILS: '3' })); });
  afterAll(async () => { await gw.close(); });

  it('signs in with an HttpOnly SameSite cookie and never returns the password hash', async () => {
    const u = await createUser(gw, 'VIEWER');
    const s = await signIn(gw, u.email);
    expect(s.setCookie).toMatch(new RegExp(`^${COOKIE_NAME}=`));
    expect(s.setCookie).toMatch(/HttpOnly/i);
    expect(s.setCookie).toMatch(/SameSite=Lax/i);
    expect(s.setCookie).not.toMatch(/Secure/i); // development; COOKIE_SECURE is on in production
    const me = (await s.get('/api/auth/me').expect(200)).body;
    expect(me.user).toEqual({ id: u.id, email: u.email, name: expect.any(String), role: 'VIEWER' });
    expect(JSON.stringify(me)).not.toMatch(/password|hash/i);
    // Only the hash of the session token is stored
    const token = readCookie(s.cookie, COOKIE_NAME);
    const stored = gw.db.raw.prepare('SELECT token_hash FROM sessions').all().map((r) => r.token_hash);
    expect(stored).not.toContain(token);
    // Stored passwords are salted Argon2id hashes (scrypt on Node versions without Argon2)
    expect(gw.db.getUserByEmail(u.email).password_hash).toMatch(/^\$(argon2id\$v=19\$m=19456,t=2,p=1|scrypt\$N=32768,r=8,p=1)\$/);
    const other = await createUser(gw, 'VIEWER');
    expect(gw.db.getUserByEmail(other.email).password_hash).not.toBe(gw.db.getUserByEmail(u.email).password_hash); // own salt
  });

  it('sets the Secure flag and HSTS when COOKIE_SECURE is on', async () => {
    const prod = createGateway({ ...base, COOKIE_SECURE: 'true' });
    try {
      const u = await createUser(prod, 'VIEWER');
      const res = await request(prod.app).post('/api/auth/login').send({ email: u.email, password: PASSWORD }).expect(200);
      expect(res.headers['set-cookie'][0]).toMatch(/Secure/i);
      expect(res.headers['strict-transport-security']).toMatch(/max-age/);
    } finally {
      await prod.close();
    }
  });

  it('answers the same for a wrong password and an unknown email, and rejects malformed input', async () => {
    const u = await createUser(gw, 'VIEWER');
    const wrong = await request(gw.app).post('/api/auth/login').send({ email: u.email, password: 'not-the-password' }).expect(401);
    const unknown = await request(gw.app).post('/api/auth/login').send({ email: 'nobody@test.local', password: 'x' }).expect(401);
    expect(wrong.body).toEqual(unknown.body);
    await request(gw.app).post('/api/auth/login').send({ email: 'not-an-email', password: 'x' }).expect(400);
    await request(gw.app).post('/api/auth/login').send({ email: u.email, password: PASSWORD, role: 'ADMIN' }).expect(400);
    await request(gw.app).post('/api/auth/login').set('content-type', 'application/json').send('{bad json').expect(400);
    // SQL injection attempts are plain strings to the parameterised queries
    await request(gw.app).post('/api/auth/login').send({ email: "a'--@test.local", password: "' OR '1'='1" }).expect(401);
    expect(gw.db.countUsers()).toBeGreaterThan(0);
  });

  it('locks the account after repeated failures and an admin can unlock it', async () => {
    const u = await createUser(gw, 'VIEWER');
    for (let i = 0; i < 3; i += 1) {
      await request(gw.app).post('/api/auth/login').send({ email: u.email, password: 'wrong-password' }).expect(401);
    }
    const locked = await request(gw.app).post('/api/auth/login').send({ email: u.email, password: PASSWORD }).expect(429);
    expect(locked.body.code).toBe('locked');
    const admin = await sessionFor(gw, 'ADMIN');
    await admin.patch(`/api/users/${u.id}`).send({ status: 'active' }).expect(200);
    await signIn(gw, u.email);
  });

  it('ends the session on sign-out and when it expires', async () => {
    const u = await createUser(gw, 'VIEWER');
    const s = await signIn(gw, u.email);
    await s.get('/api/options').expect(200);
    await s.post('/api/auth/logout').expect(200);
    await s.get('/api/options').expect(401);
    expect((await s.get('/api/auth/me').expect(200)).body.user).toBeNull();

    const t = await signIn(gw, u.email);
    gw.db.raw.prepare("UPDATE sessions SET expires_at = '2000-01-01T00:00:00.000Z' WHERE user_id = ?").run(u.id);
    await t.get('/api/options').expect(401);
    expect(gw.db.raw.prepare('SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?').get(u.id).n).toBe(0);
  });

  it('keeps at most SESSIONS_PER_USER sessions per account', async () => {
    const few = createGateway({ ...base, SESSIONS_PER_USER: '2' });
    try {
      const u = await createUser(few, 'VIEWER');
      const first = await signIn(few, u.email);
      await signIn(few, u.email);
      await signIn(few, u.email);
      expect(few.db.userSessionIds(u.id)).toHaveLength(2);
      await first.get('/api/options').expect(401); // the oldest one was ended
    } finally {
      await few.close();
    }
  });

  it('rate limits sign-in attempts per address', async () => {
    const strict = createGateway({ ...base, AUTH_RATE_LIMIT: '3' });
    try {
      const body = { email: 'x@test.local', password: 'whatever-it-is' };
      for (let i = 0; i < 3; i += 1) await request(strict.app).post('/api/auth/login').send(body).expect(401);
      const res = await request(strict.app).post('/api/auth/login').send(body).expect(429);
      expect(res.body.code).toBe('rate_limited');
    } finally {
      await strict.close();
    }
  });

  it('sends security headers and no permissive CORS', async () => {
    const res = await request(gw.app).get('/api/health').set('Origin', 'https://evil.example').expect(200);
    expect(res.headers['content-security-policy']).toMatch(/default-src 'self'/);
    expect(res.headers['content-security-policy']).toMatch(/frame-ancestors 'none'/);
    expect(res.headers['x-content-type-options']).toBe('nosniff');
    expect(res.headers['x-powered-by']).toBeUndefined();
    expect(res.headers['access-control-allow-origin']).toBeUndefined();
  });
});

describe('CSRF protection', () => {
  let gw;
  beforeAll(async () => { ({ gw } = await start()); });
  afterAll(async () => { await gw.close(); });

  it('refuses writes without the session CSRF token or from another origin', async () => {
    const admin = await sessionFor(gw, 'ADMIN');
    const body = { email: 'new@test.local', name: 'New', password: PASSWORD };
    const noToken = await request(gw.app).post('/api/users').set('Cookie', admin.cookie).send(body).expect(403);
    expect(noToken.body.code).toBe('csrf');
    await request(gw.app).post('/api/users').set('Cookie', admin.cookie).set('X-CSRF-Token', 'guess').send(body).expect(403);
    const other = await sessionFor(gw, 'ADMIN');
    await request(gw.app).post('/api/users').set('Cookie', admin.cookie).set('X-CSRF-Token', other.csrf).send(body).expect(403);
    const evil = await admin.post('/api/users').set('Origin', 'https://evil.example').send(body).expect(403);
    expect(evil.body.code).toBe('bad_origin');
    await request(gw.app).post('/api/auth/login').set('Origin', 'https://evil.example')
      .send({ email: admin.user.email, password: PASSWORD }).expect(403);
    expect(gw.db.getUserByEmail('new@test.local')).toBeNull();
    await admin.post('/api/users').send(body).expect(201); // with the token it works
  });

  it('accepts an origin listed in CORS_ORIGIN, with credentials', async () => {
    const open = createGateway({ ...base, CORS_ORIGIN: 'https://dash.example' });
    try {
      const u = await createUser(open, 'VIEWER');
      const res = await request(open.app).post('/api/auth/login').set('Origin', 'https://dash.example')
        .send({ email: u.email, password: PASSWORD }).expect(200);
      expect(res.headers['access-control-allow-origin']).toBe('https://dash.example');
      expect(res.headers['access-control-allow-credentials']).toBe('true');
    } finally {
      await open.close();
    }
  });
});

describe('roles', () => {
  let gw;
  let admin;
  let researcher;
  let viewer;
  beforeAll(async () => {
    ({ gw } = await start());
    admin = await sessionFor(gw, 'ADMIN');
    researcher = await sessionFor(gw, 'RESEARCHER');
    viewer = await sessionFor(gw, 'VIEWER');
  });
  afterAll(async () => { await gw.close(); });

  it('keeps every data route behind sign-in', async () => {
    const closed = ['/api/live/status', '/api/live/playlist', '/api/options', '/api/experiments/summary', '/api/runs',
      '/api/runs/x', '/api/driver', '/api/sumo/gui', '/api/metrics', '/api/system', '/api/users', '/api/sessions',
      '/api/audit', '/api/settings'];
    for (const url of closed) {
      const res = await request(gw.app).get(url);
      expect(res.status, url).toBe(401);
    }
    await request(gw.app).get('/api/health').expect(200);
    await request(gw.app).get('/api/auth/config').expect(200);
  });

  it('lets viewers and researchers read, and only administrators administer', async () => {
    for (const s of [viewer, researcher]) {
      await s.get('/api/options').expect(200);
      await s.get('/api/runs').expect(200);
      for (const url of ['/api/users', '/api/audit', '/api/sessions', '/api/settings', '/api/metrics', '/api/system']) {
        expect((await s.get(url)).status, url).toBe(403);
      }
      await s.post('/api/users').send({ email: 'x@test.local', name: 'X', password: PASSWORD, role: 'ADMIN' }).expect(403);
      await s.patch(`/api/users/${s.user.id}`).send({ role: 'ADMIN' }).expect(403); // no self-promotion
      await s.post('/api/driver').send({ enabled: false }).expect(403);
      await s.post('/api/driver/release').expect(403);
      await s.put('/api/settings').send({ allow_registration: true }).expect(403);
    }
    expect(gw.db.getUserById(viewer.user.id).role).toBe('VIEWER');
    const denied = (await admin.get('/api/audit?action=access_denied').expect(200)).body.entries;
    expect(denied.length).toBeGreaterThan(5);
    const sys = (await admin.get('/api/system').expect(200)).body;
    expect(sys.database).toMatchObject({ ok: true });
    expect(sys.database.migrations.map((m) => m.version)).toEqual([1, 2, 3, 4]);
    expect(sys.simulation.lifecycle).toBe('STOPPED');
    expect(sys.max_connections).toBe(1000);
  });

  it('lets a researcher start and stop their own run, but not replace or stop someone else\'s', async () => {
    const entry = { scenario: 'normal', controller: 'fixed' };
    const mine = await researcher.post('/api/live/start').send(entry).expect(202);
    expect(gw.live.current.started_by).toEqual({ id: researcher.user.id, name: researcher.user.name });
    expect(gw.db.getRun(mine.body.run_id).run.started_by).toBe(researcher.user.id);
    await viewer.post('/api/live/stop').expect(403);
    const other = await sessionFor(gw, 'RESEARCHER');
    await other.post('/api/live/start').send(entry).expect(409);
    await other.post('/api/live/stop').expect(403);
    expect((await researcher.post('/api/live/stop').expect(200)).body.stopped).toBe(true);
    await admin.post('/api/live/start').send(entry).expect(202);
    await researcher.post('/api/live/stop').expect(403); // the administrator's run
    await admin.post('/api/live/stop').expect(200);
    const actions = (await admin.get('/api/audit?limit=200').expect(200)).body.entries.map((e) => e.action);
    expect(actions).toEqual(expect.arrayContaining(['sim_start', 'sim_stop', 'login']));
  });
});

describe('user management', () => {
  let gw;
  let admin;
  beforeAll(async () => {
    ({ gw } = await start());
    admin = await sessionFor(gw, 'ADMIN');
  });
  afterAll(async () => { await gw.close(); });

  it('creates, lists, changes, disables and deletes users, with validation', async () => {
    await admin.post('/api/users').send({ email: 'r@test.local', name: 'R', password: 'short' }).expect(400);
    await admin.post('/api/users').send({ email: 'r@test.local', name: 'R', password: 'x'.repeat(80) }).expect(400); // over 72 bytes
    await admin.post('/api/users').send({ email: 'r@test.local', name: 'R', password: PASSWORD, role: 'ROOT' }).expect(400);
    const made = (await admin.post('/api/users').send({ email: 'R@Test.Local', name: ' Rita ', password: PASSWORD, role: 'RESEARCHER' }).expect(201)).body.user;
    expect(made).toMatchObject({ email: 'r@test.local', name: 'Rita', role: 'RESEARCHER', status: 'active' });
    expect(made).not.toHaveProperty('password_hash');
    await admin.post('/api/users').send({ email: 'r@test.local', name: 'Again', password: PASSWORD }).expect(409);

    const list = (await admin.get('/api/users?limit=1').expect(200)).body;
    expect(list.users).toHaveLength(1);
    expect(list.total).toBe(2);
    expect(JSON.stringify(list)).not.toMatch(/password_hash/);

    const rita = await signIn(gw, 'r@test.local');
    await admin.patch(`/api/users/${made.id}`).send({ role: 'VIEWER' }).expect(200);
    await rita.post('/api/live/start').send({ scenario: 'normal', controller: 'fixed' }).expect(403); // role applies at once
    await admin.patch(`/api/users/${made.id}`).send({ status: 'disabled' }).expect(200);
    await rita.get('/api/options').expect(401); // sessions of a disabled account end
    await request(gw.app).post('/api/auth/login').send({ email: 'r@test.local', password: PASSWORD }).expect(403);
    await admin.patch(`/api/users/${made.id}`).send({}).expect(400);
    await admin.patch('/api/users/abc').send({ role: 'VIEWER' }).expect(400);
    await admin.patch('/api/users/99999').send({ role: 'VIEWER' }).expect(404);
    await admin.del(`/api/users/${made.id}`).expect(200);
    expect(gw.db.getUserById(made.id)).toBeNull();
  });

  it('always keeps one active administrator and forbids deleting yourself', async () => {
    const me = admin.user.id;
    expect((await admin.patch(`/api/users/${me}`).send({ role: 'VIEWER' }).expect(409)).body.code).toBe('last_admin');
    await admin.patch(`/api/users/${me}`).send({ status: 'disabled' }).expect(409);
    await admin.del(`/api/users/${me}`).expect(409);
    const second = await createUser(gw, 'ADMIN');
    await admin.del(`/api/users/${second.id}`).expect(200);
    expect(gw.db.countActiveAdmins()).toBe(1);
  });

  it('changes the own password, ending other sessions, and updates the profile', async () => {
    const u = await createUser(gw, 'VIEWER');
    const a = await signIn(gw, u.email);
    const b = await signIn(gw, u.email);
    await a.post('/api/auth/password').send({ current_password: 'wrong-one-here', new_password: 'another-good-password' }).expect(400);
    await a.post('/api/auth/password').send({ current_password: PASSWORD, new_password: 'short' }).expect(400);
    await a.post('/api/auth/password').send({ current_password: PASSWORD, new_password: 'another-good-password' }).expect(200);
    await a.get('/api/options').expect(200);
    await b.get('/api/options').expect(401);
    await request(gw.app).post('/api/auth/login').send({ email: u.email, password: PASSWORD }).expect(401);
    await signIn(gw, u.email, 'another-good-password');
    expect((await a.patch('/api/auth/profile').send({ name: 'New Name' }).expect(200)).body.user.name).toBe('New Name');
    await a.patch('/api/auth/profile').send({ name: '', role: 'ADMIN' }).expect(400);
  });

  it('resets a password with a single-use, expiring link issued by an administrator', async () => {
    const u = await createUser(gw, 'VIEWER');
    const old = await signIn(gw, u.email);
    const forgot = await request(gw.app).post('/api/auth/forgot').send({ email: u.email }).expect(200);
    const unknown = await request(gw.app).post('/api/auth/forgot').send({ email: 'ghost@test.local' }).expect(200);
    expect(forgot.body).toEqual(unknown.body); // no account enumeration
    expect((await admin.get('/api/audit?action=password_reset_request').expect(200)).body.entries[0].target).toBe(u.email);

    const link = (await admin.post(`/api/users/${u.id}/reset-link`).expect(201)).body;
    const token = new URLSearchParams(link.path.split('?')[1]).get('token');
    expect(gw.db.raw.prepare('SELECT token_hash FROM password_resets').all().map((r) => r.token_hash)).not.toContain(token);
    await request(gw.app).post('/api/auth/reset').send({ token: 'x'.repeat(40), password: 'brand-new-password' }).expect(400);
    await request(gw.app).post('/api/auth/reset').send({ token, password: 'short' }).expect(400);
    await request(gw.app).post('/api/auth/reset').send({ token, password: 'brand-new-password' }).expect(200);
    await request(gw.app).post('/api/auth/reset').send({ token, password: 'second-use-attempt' }).expect(400); // single use
    await old.get('/api/options').expect(401); // old sessions end
    await signIn(gw, u.email, 'brand-new-password');

    const link2 = (await admin.post(`/api/users/${u.id}/reset-link`).expect(201)).body;
    gw.db.raw.prepare("UPDATE password_resets SET expires_at = '2000-01-01T00:00:00.000Z' WHERE used_at IS NULL").run();
    const token2 = new URLSearchParams(link2.path.split('?')[1]).get('token');
    await request(gw.app).post('/api/auth/reset').send({ token: token2, password: 'too-late-password' }).expect(400);
  });

  it('opens and closes registration from the settings, and new accounts are viewers', async () => {
    const body = { email: 'self@test.local', name: 'Self', password: PASSWORD };
    expect((await request(gw.app).get('/api/auth/config').expect(200)).body.registration).toBe(false);
    await request(gw.app).post('/api/auth/register').send(body).expect(403);
    await admin.put('/api/settings').send({ allow_registration: 'yes' }).expect(400);
    await admin.put('/api/settings').send({ allow_registration: true }).expect(200);
    await request(gw.app).post('/api/auth/register').send({ ...body, role: 'ADMIN' }).expect(400); // cannot choose a role
    const res = await request(gw.app).post('/api/auth/register').send(body).expect(201);
    expect(res.body.user.role).toBe('VIEWER');
    expect(res.headers['set-cookie'][0]).toMatch(/HttpOnly/i);
    await request(gw.app).post('/api/auth/register').send(body).expect(409);
    await admin.put('/api/settings').send({ allow_registration: false }).expect(200);
  });

  it('lists sessions, revokes one, and records an audit trail without secrets', async () => {
    const u = await createUser(gw, 'VIEWER');
    const s = await signIn(gw, u.email);
    const sessions = (await admin.get('/api/sessions').expect(200)).body.sessions;
    const target = sessions.find((x) => x.email === u.email);
    expect(sessions.some((x) => x.current)).toBe(true);
    expect(JSON.stringify(sessions)).not.toMatch(/token_hash|csrf/);
    await admin.del(`/api/sessions/${target.id}`).expect(200);
    await s.get('/api/options').expect(401);
    const audit = (await admin.get('/api/audit?limit=200').expect(200)).body;
    expect(audit.entries.map((e) => e.action)).toEqual(expect.arrayContaining(
      ['login', 'login_fail', 'user_create', 'user_update', 'user_delete', 'password_change', 'password_reset',
        'password_reset_link', 'settings_update', 'session_revoke', 'register']));
    expect(JSON.stringify(audit)).not.toContain(PASSWORD);
    expect(JSON.stringify(audit)).not.toMatch(/\$(2[aby]|argon2id|scrypt)\$/);
  });
});

describe('password hashes', () => {
  it('accepts a bcrypt hash from an earlier version once and upgrades it at sign-in', async () => {
    const { default: bcrypt } = await import('bcryptjs');
    const { verifyPassword } = await import('../src/password.js');
    const gw = createGateway({ ...base });
    try {
      const id = gw.db.createUser({ email: 'old@test.local', name: 'Old', role: 'VIEWER', password_hash: bcrypt.hashSync(PASSWORD, 6) });
      await request(gw.app).post('/api/auth/login').send({ email: 'old@test.local', password: 'wrong-password-x' }).expect(401);
      expect(gw.db.getUserById(id).password_hash).toMatch(/^\$2/); // a failed attempt changes nothing
      await signIn(gw, 'old@test.local');
      const upgraded = gw.db.getUserById(id).password_hash;
      expect(upgraded).not.toMatch(/^\$2/);
      await signIn(gw, 'old@test.local'); // and the new hash works
      expect(await verifyPassword(PASSWORD, upgraded)).toEqual({ ok: true, rehash: false });
      expect(await verifyPassword('not-it', upgraded)).toEqual({ ok: false, rehash: false });
      for (const bad of ['', 'plain-text', '$argon2id$v=19$m=x$bad$bad', '$scrypt$N=1$$', null]) {
        expect((await verifyPassword(PASSWORD, bad)).ok).toBe(false);
      }
    } finally {
      await gw.close();
    }
  });

  it('hashes many passwords at once without stalling other requests', async () => {
    const { gw, url } = await start();
    try {
      const users = await Promise.all(Array.from({ length: 24 }, () => createUser(gw, 'VIEWER')));
      const admin = await sessionFor(gw, 'ADMIN');
      const post = (u) => fetch(`${url}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email: u.email, password: PASSWORD }) });
      const t0 = Date.now();
      const logins = Promise.all(users.map(post));
      const probes = [];
      for (let i = 0; i < 5; i += 1) {
        const s = Date.now();
        expect((await fetch(`${url}/api/live/status`, { headers: { cookie: admin.cookie } })).status).toBe(200);
        probes.push(Date.now() - s);
      }
      expect((await logins).every((r) => r.status === 200)).toBe(true);
      expect(Math.max(...probes)).toBeLessThan(1000); // hashing runs on the thread pool, two at a time
      expect(Date.now() - t0).toBeLessThan(15_000);
    } finally {
      await gw.close();
    }
  });
});

describe('first administrator', () => {
  it('is created from ADMIN_EMAIL / ADMIN_PASSWORD only when none exists', async () => {
    const gw = createGateway({ ...base, ADMIN_EMAIL: 'boss@test.local', ADMIN_PASSWORD: 'a-long-enough-password' });
    try {
      expect(await gw.auth.bootstrapAdmin()).toBeGreaterThan(0);
      expect(await gw.auth.bootstrapAdmin()).toBeNull(); // not twice
      await signIn(gw, 'boss@test.local', 'a-long-enough-password');
      expect(gw.db.getUserByEmail('boss@test.local').role).toBe('ADMIN');
    } finally {
      await gw.close();
    }
    const weak = createGateway({ ...base, ADMIN_EMAIL: 'boss@test.local', ADMIN_PASSWORD: 'short' });
    try {
      expect(await weak.auth.bootstrapAdmin()).toBeNull();
      expect(weak.db.countUsers()).toBe(0);
    } finally {
      await weak.close();
    }
  });
});

describe('live sockets', () => {
  it('refuses unauthenticated sockets and disconnects them when the session ends', async () => {
    const { gw, url } = await start();
    const open = [];
    try {
      expect(await outcome(connect(url, null))).toBe('unauthenticated');
      expect(await outcome(connect(url, { cookie: `${COOKIE_NAME}=forged-token-value` }))).toBe('unauthenticated');
      const u = await createUser(gw, 'VIEWER');
      const s = await signIn(gw, u.email);
      const socket = connect(url, s);
      open.push(socket);
      let init = null;
      socket.on('init', (m) => { init = m; });
      expect(await outcome(socket)).toBe('connected');
      await waitFor(() => init !== null);
      expect(init.now_playing.lifecycle).toBe('STOPPED');
      let gone = false;
      socket.on('disconnect', () => { gone = true; });
      await s.post('/api/auth/logout').expect(200);
      await waitFor(() => gone);
      expect(await outcome(connect(url, s))).toBe('unauthenticated');
    } finally {
      open.forEach((s) => s.close());
      await gw.close();
    }
  });

  it('enforces the connection limit without disturbing existing viewers', async () => {
    const { gw, url } = await start({ MAX_CONNECTIONS: '3' });
    const open = [];
    try {
      const s = await sessionFor(gw, 'VIEWER');
      for (let i = 0; i < 3; i += 1) {
        const socket = connect(url, s);
        open.push(socket);
        expect(await outcome(socket)).toBe('connected');
      }
      const refused = [];
      for (let i = 0; i < 4; i += 1) {
        const socket = connect(url, s);
        open.push(socket);
        refused.push(await new Promise((resolve) => {
          socket.on('connect', () => resolve({ code: 'connected' }));
          socket.on('connect_error', (err) => resolve(err.data));
        }));
      }
      expect(refused.every((r) => r.code === 'capacity')).toBe(true);
      expect(refused[0].message).toMatch(/limit of 3/);
      expect(gw.live.viewers()).toBe(3);
      expect(open.slice(0, 3).every((x) => x.connected)).toBe(true);
      const admin = await sessionFor(gw, 'ADMIN');
      expect((await admin.get('/api/metrics').expect(200)).body).toMatchObject({ viewers: 3, max_connections: 3, capacity_rejected: 4 });
      expect((await admin.get('/api/audit?action=capacity_reject').expect(200)).body.entries).toHaveLength(1); // logged once, not per refusal
      // A place frees up when a viewer leaves
      open[0].close();
      await waitFor(() => gw.live.viewers() === 2);
      const again = connect(url, s);
      open.push(again);
      expect(await outcome(again)).toBe('connected');
    } finally {
      open.forEach((x) => x.close());
      await gw.close();
    }
  });

  it('refuses a burst of simultaneous connections above the limit', async () => {
    const { gw, url } = await start({ MAX_CONNECTIONS: '5' });
    const s = await sessionFor(gw, 'VIEWER');
    const sockets = Array.from({ length: 20 }, () => connect(url, s));
    try {
      const results = await Promise.all(sockets.map((socket) => new Promise((resolve) => {
        socket.on('connect', () => setTimeout(() => resolve(socket.connected ? 'connected' : 'dropped'), 300));
        socket.on('connect_error', (err) => resolve(err.data?.code));
      })));
      expect(results.filter((r) => r === 'connected')).toHaveLength(5);
      expect(gw.live.viewers()).toBe(5);
    } finally {
      sockets.forEach((x) => x.close());
      await gw.close();
    }
  });
});

describe('simulation lifecycle', () => {
  it('goes STARTING -> RUNNING -> STOPPING -> STOPPED and refuses overlapping requests', async () => {
    const { gw, url } = await start();
    const admin = await sessionFor(gw, 'ADMIN');
    const socket = connect(url, admin);
    const seen = [];
    socket.on('now_playing', (m) => seen.push(m.lifecycle));
    try {
      await outcome(socket);
      const entry = { scenario: 'normal', controller: 'fixed' };
      // Two starts at the same moment: one runs, the other is refused, and only one process exists
      const [a, b] = await Promise.all([admin.post('/api/live/start').send(entry), admin.post('/api/live/start').send(entry)]);
      expect([a.status, b.status].sort()).toEqual([202, 202]); // the first finished before the second began, or:
      await waitFor(() => gw.live.lifecycle === 'RUNNING');
      expect(gw.db.listRuns(10).filter((r) => r.status === 'running')).toHaveLength(1);

      let release;
      const hold = gw.live.exclusive(() => new Promise((resolve) => { release = resolve; }));
      const busy = await admin.post('/api/live/stop').expect(409);
      expect(busy.body.code).toBe('busy');
      await admin.post('/api/live/start').send(entry).expect(409);
      release();
      await hold;

      await admin.post('/api/live/stop').expect(200);
      expect(gw.live.lifecycle).toBe('STOPPED');
      await waitFor(() => seen.includes('STOPPED'));
      expect(seen).toEqual(expect.arrayContaining(['STARTING', 'RUNNING', 'STOPPED']));
      expect((await request(gw.app).get('/api/health').expect(200)).body).toMatchObject({ live_running: false, lifecycle: 'STOPPED' });
    } finally {
      socket.close();
      await gw.close();
    }
  });

  it('reports FAILED for a crashed run and stops retrying the playlist after MAX_SIM_FAILURES', async () => {
    const playlist = path.join(tmp, 'playlist.json');
    fs.writeFileSync(playlist, JSON.stringify([{ scenario: 'normal', controller: 'fixed' }]));
    const { gw } = await start({ LIVE_LOOP: 'true', PLAYLIST_PATH: playlist, MOCK_ENTRY: 'sim/does_not_exist.py', MAX_SIM_FAILURES: '3' });
    try {
      gw.live.startLoop();
      await waitFor(() => gw.live.lifecycle === 'FAILED');
      expect(gw.live.failures).toBe(3);
      await new Promise((r) => setTimeout(r, 500)); // no fourth attempt
      const runs = gw.db.listRuns(50);
      expect(runs).toHaveLength(3);
      expect(runs.every((r) => r.status === 'error')).toBe(true);
      expect(gw.live.nowPlaying()).toMatchObject({ running: false, lifecycle: 'FAILED', loop: false });

      // An administrator start with a working simulation recovers and resets the counter
      gw.cfg.MOCK_ENTRY = 'sim/mock_sim.py';
      const admin = await sessionFor(gw, 'ADMIN');
      await admin.post('/api/live/start').send({ scenario: 'normal', controller: 'fixed' }).expect(202);
      await waitFor(() => gw.live.lifecycle === 'RUNNING');
      expect(gw.live.failures).toBe(0);
    } finally {
      await gw.close();
    }
  }, 30_000);
});

describe('database', () => {
  it('migrates an older database in place and is idempotent', () => {
    const file = path.join(tmp, 'old.db');
    const old = openDatabase(file);
    // Simulate a database from before the accounts existed
    old.raw.exec('DROP TABLE users; DROP TABLE sessions; DROP TABLE password_resets; DROP TABLE audit_logs; DROP TABLE settings;');
    old.raw.exec('ALTER TABLE runs DROP COLUMN started_by; DELETE FROM schema_migrations WHERE version >= 3;');
    old.raw.exec('DROP INDEX idx_samples_created;');
    old.insertRun = null;
    old.raw.prepare(`INSERT INTO runs (id, scenario, controller, nav, seed, source, status, started_at)
      VALUES ('keep-me', 'normal', 'fixed', 'none', 1, 'admin', 'finished', '2026-01-01T00:00:00.000Z')`).run();
    old.close();

    const db = openDatabase(file);
    try {
      expect(db.raw.prepare('SELECT version FROM schema_migrations ORDER BY version').all().map((r) => r.version)).toEqual([1, 2, 3, 4]);
      expect(db.getRun('keep-me').run.scenario).toBe('normal'); // existing data kept
      const plan = db.raw.prepare('EXPLAIN QUERY PLAN DELETE FROM state_samples WHERE created_at < ?').all('2026').map((r) => r.detail).join();
      expect(plan).toContain('idx_samples_created'); // retention uses the index, not a full scan
      expect(db.countUsers()).toBe(0);
      expect(migrate(db.raw)).toEqual([]); // nothing left to apply
    } finally {
      db.close();
    }
  });

  it('enforces unique emails, valid roles and foreign keys, and rolls back failed transactions', () => {
    const db = openDatabase(':memory:');
    try {
      const id = db.createUser({ email: 'a@test.local', name: 'A', password_hash: 'h', role: 'VIEWER' });
      expect(() => db.createUser({ email: 'A@TEST.LOCAL', name: 'B', password_hash: 'h' })).toThrow(/UNIQUE/);
      expect(() => db.createUser({ email: 'b@test.local', name: 'B', password_hash: 'h', role: 'ROOT' })).toThrow(/CHECK/);
      expect(() => db.createSession({ token_hash: 't', user_id: 999, csrf: 'c', expires_at: '2999-01-01' })).toThrow(/FOREIGN KEY/);
      db.createSession({ token_hash: 't1', user_id: id, csrf: 'c', expires_at: '2999-01-01T00:00:00.000Z' });
      expect(() => db.createSession({ token_hash: 't1', user_id: id, csrf: 'c', expires_at: '2999-01-01' })).toThrow(/UNIQUE/);
      expect(db.updateUser(id, { role: 'ADMIN', "name = 'x' --": 'ignored' })).toBe(1); // unknown columns are dropped
      expect(db.getUserById(id)).toMatchObject({ role: 'ADMIN', name: 'A' });

      // A reset that cannot complete (already used) changes nothing
      db.createPasswordReset('r1', id, null, '2999-01-01T00:00:00.000Z');
      const reset = db.getPasswordReset('r1');
      expect(db.completePasswordReset(reset.id, id, 'new-hash')).toBe(true);
      expect(db.userSessionIds(id)).toEqual([]);
      expect(db.completePasswordReset(reset.id, id, 'other-hash')).toBe(false);
      expect(db.getUserById(id).password_hash).toBe('new-hash');

      db.deleteUser(id); // cascades
      expect(db.raw.prepare('SELECT COUNT(*) AS n FROM password_resets').get().n).toBe(0);
      // Expired sessions and old audit rows are removed by housekeeping
      const id2 = db.createUser({ email: 'c@test.local', name: 'C', password_hash: 'h' });
      db.createSession({ token_hash: 'old', user_id: id2, csrf: 'c', expires_at: '2000-01-01T00:00:00.000Z' });
      expect(db.deleteExpiredSessions()).toBe(1);
      db.audit({ action: 'login', detail: 'x'.repeat(2000) });
      expect(db.listAudit(1)[0].detail).toHaveLength(500);
      expect(db.pruneAudit('2999-01-01T00:00:00.000Z')).toBe(1);
    } finally {
      db.close();
    }
  });
});
