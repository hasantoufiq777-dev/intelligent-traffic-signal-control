// SQLite persistence: runs, final results, events, state samples (one per SAMPLE_EVERY_SIM_S) and driver trips.
import fs from 'node:fs';
import path from 'node:path';
import Database from 'better-sqlite3';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS runs (
  id          TEXT PRIMARY KEY,
  scenario    TEXT NOT NULL,
  controller  TEXT NOT NULL,
  nav         TEXT NOT NULL DEFAULT 'none',
  seed        INTEGER NOT NULL,
  penetration REAL,
  loss        REAL,
  relay       TEXT,
  eco_fraction REAL DEFAULT 0,
  ev_routing  TEXT DEFAULT 'off',
  source      TEXT NOT NULL,            -- admin | playlist
  status      TEXT NOT NULL,            -- running | finished | stopped | error | interrupted
  message     TEXT,
  started_at  TEXT NOT NULL,
  ended_at    TEXT
);
CREATE TABLE IF NOT EXISTS results (
  run_id      TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
  metrics     TEXT NOT NULL,            -- JSON of the result metrics
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  t           INTEGER,
  kind        TEXT,
  tls         TEXT,
  detail      TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS state_samples (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  t           INTEGER,
  payload     TEXT NOT NULL,            -- JSON of the full state message
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples_run ON state_samples(run_id, t);
CREATE TABLE IF NOT EXISTS driver_trips (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  vid         TEXT,                     -- simulation vehicle id (drv1, ...)
  from_edge   TEXT NOT NULL,
  to_edge     TEXT NOT NULL,
  status      TEXT NOT NULL,            -- driving | finished | released | error
  reason      TEXT,                     -- why it ended: finished | released | idle | left | admin | time_limit | run_ended | error
  report      TEXT,                     -- JSON trip report from the simulation
  started_at  TEXT NOT NULL,
  ended_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_driver_trips_run ON driver_trips(run_id, id);
`;

// Accounts, sessions and audit trail (migration 3)
const AUTH_SCHEMA = `
CREATE TABLE users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name          TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('ADMIN', 'RESEARCHER', 'VIEWER')),
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until  TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  last_login_at TEXT
);
CREATE TABLE sessions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash   TEXT NOT NULL UNIQUE,     -- SHA-256 of the cookie value; the value itself is never stored
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf         TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  ip           TEXT,
  user_agent   TEXT
);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);
CREATE TABLE password_resets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash  TEXT NOT NULL UNIQUE,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_by  INTEGER,                   -- admin who issued the link
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  used_at     TEXT
);
CREATE TABLE audit_logs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  user_id     INTEGER,                   -- kept after the user is deleted (no foreign key)
  user_email  TEXT,
  action      TEXT NOT NULL,
  target      TEXT,
  detail      TEXT,
  ip          TEXT
);
CREATE INDEX idx_audit_at ON audit_logs(at);
CREATE INDEX idx_audit_action ON audit_logs(action, id);
CREATE TABLE settings (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
ALTER TABLE runs ADD COLUMN started_by INTEGER;
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
`;

/** Ordered schema migrations; each runs once, in a transaction, and is recorded in schema_migrations. */
const MIGRATIONS = [
  { version: 1, name: 'runs, results, events, state samples, driver trips', up: (db) => db.exec(SCHEMA) },
  { version: 2, name: 'runs.eco_fraction, runs.ev_routing', up: (db) => {
    const cols = db.prepare('PRAGMA table_info(runs)').all().map((c) => c.name);
    if (!cols.includes('eco_fraction')) db.exec('ALTER TABLE runs ADD COLUMN eco_fraction REAL DEFAULT 0');
    if (!cols.includes('ev_routing')) db.exec("ALTER TABLE runs ADD COLUMN ev_routing TEXT DEFAULT 'off'");
  } },
  { version: 3, name: 'users, sessions, password resets, audit log, settings, runs.started_by', up: (db) => db.exec(AUTH_SCHEMA) },
  // The retention job deletes samples by age; without this index it read the whole table every time
  { version: 4, name: 'index state_samples.created_at (retention)', up: (db) =>
    db.exec('CREATE INDEX IF NOT EXISTS idx_samples_created ON state_samples(created_at)') },
];

export function migrate(db) {
  db.exec('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)');
  const done = new Set(db.prepare('SELECT version FROM schema_migrations').all().map((r) => r.version));
  const record = db.prepare('INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)');
  const applied = [];
  for (const m of MIGRATIONS) {
    if (done.has(m.version)) continue;
    db.transaction(() => {
      m.up(db);
      record.run(m.version, m.name, new Date().toISOString());
    })();
    applied.push(m.version);
  }
  return applied;
}

const now = () => new Date().toISOString();
const PUBLIC_USER = 'id, email, name, role, status, created_at, updated_at, last_login_at, locked_until';

export function openDatabase(dbPath) {
  if (dbPath !== ':memory:') fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  const db = new Database(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  db.pragma('busy_timeout = 5000');
  migrate(db);

  const stmts = {
    insertRun: db.prepare(`INSERT INTO runs (id, scenario, controller, nav, seed, penetration, loss, relay, eco_fraction, ev_routing, source, status, started_at, started_by)
      VALUES (@id, @scenario, @controller, @nav, @seed, @penetration, @loss, @relay, @eco_fraction, @ev_routing, @source, 'running', @started_at, @started_by)`),
    endRun: db.prepare(`UPDATE runs SET status = @status, message = @message, ended_at = @ended_at WHERE id = @id AND status = 'running'`),
    markInterrupted: db.prepare(`UPDATE runs SET status = 'interrupted', message = 'gateway restarted while running', ended_at = ? WHERE status = 'running'`),
    upsertResult: db.prepare(`INSERT INTO results (run_id, metrics, created_at) VALUES (?, ?, ?)
      ON CONFLICT(run_id) DO UPDATE SET metrics = excluded.metrics, created_at = excluded.created_at`),
    insertEvent: db.prepare(`INSERT INTO events (run_id, t, kind, tls, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)`),
    insertSample: db.prepare(`INSERT INTO state_samples (run_id, t, payload, created_at) VALUES (?, ?, ?, ?)`),
    listRuns: db.prepare(`SELECT r.*, (res.run_id IS NOT NULL) AS has_result FROM runs r
      LEFT JOIN results res ON res.run_id = r.id ORDER BY r.started_at DESC LIMIT ? OFFSET ?`),
    getRun: db.prepare(`SELECT * FROM runs WHERE id = ?`),
    getResult: db.prepare(`SELECT metrics, created_at FROM results WHERE run_id = ?`),
    getEvents: db.prepare(`SELECT t, kind, tls, detail FROM events WHERE run_id = ? ORDER BY id LIMIT ?`),
    countSamples: db.prepare(`SELECT COUNT(*) AS n FROM state_samples WHERE run_id = ?`),
    getSamples: db.prepare(`SELECT payload FROM state_samples WHERE run_id = ? ORDER BY t LIMIT ?`),
    insertTrip: db.prepare(`INSERT INTO driver_trips (run_id, from_edge, to_edge, status, started_at) VALUES (?, ?, ?, 'driving', ?)`),
    endTrip: db.prepare(`UPDATE driver_trips SET status = @status, reason = COALESCE(reason, @reason), vid = COALESCE(@vid, vid),
      report = COALESCE(@report, report), ended_at = COALESCE(ended_at, @ended_at) WHERE id = @id`),
    listTrips: db.prepare(`SELECT * FROM driver_trips ORDER BY id DESC LIMIT ?`),
    closeOpenTrips: db.prepare(`UPDATE driver_trips SET status = 'released', reason = 'run_ended', ended_at = ? WHERE status = 'driving'`),
    countRuns: db.prepare('SELECT COUNT(*) AS n FROM runs'),
    pruneSamples: db.prepare('DELETE FROM state_samples WHERE created_at < ?'),
    countAllSamples: db.prepare('SELECT COUNT(*) AS n FROM state_samples'),
    // ---- accounts
    insertUser: db.prepare(`INSERT INTO users (email, name, password_hash, role, status, created_at, updated_at)
      VALUES (@email, @name, @password_hash, @role, @status, @at, @at)`),
    userByEmail: db.prepare('SELECT * FROM users WHERE email = ?'),
    userById: db.prepare('SELECT * FROM users WHERE id = ?'),
    listUsers: db.prepare(`SELECT ${PUBLIC_USER} FROM users ORDER BY id LIMIT ? OFFSET ?`),
    countUsers: db.prepare('SELECT COUNT(*) AS n FROM users'),
    countActiveAdmins: db.prepare("SELECT COUNT(*) AS n FROM users WHERE role = 'ADMIN' AND status = 'active'"),
    deleteUser: db.prepare('DELETE FROM users WHERE id = ?'),
    loginFail: db.prepare('UPDATE users SET failed_logins = failed_logins + 1, locked_until = @locked_until WHERE id = @id'),
    loginOk: db.prepare('UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = ? WHERE id = ?'),
    // ---- sessions
    insertSession: db.prepare(`INSERT INTO sessions (token_hash, user_id, csrf, created_at, last_seen_at, expires_at, ip, user_agent)
      VALUES (@token_hash, @user_id, @csrf, @at, @at, @expires_at, @ip, @user_agent)`),
    sessionByHash: db.prepare(`SELECT s.id AS session_id, s.csrf, s.expires_at, s.last_seen_at, s.created_at AS session_created_at,
      u.id, u.email, u.name, u.role, u.status FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?`),
    touchSession: db.prepare('UPDATE sessions SET last_seen_at = ? WHERE id = ?'),
    deleteSession: db.prepare('DELETE FROM sessions WHERE id = ?'),
    deleteUserSessions: db.prepare('DELETE FROM sessions WHERE user_id = ?'),
    userSessionIds: db.prepare('SELECT id FROM sessions WHERE user_id = ? ORDER BY id'),
    deleteExpiredSessions: db.prepare('DELETE FROM sessions WHERE expires_at <= ?'),
    listSessions: db.prepare(`SELECT s.id, s.user_id, u.email, u.role, s.created_at, s.last_seen_at, s.expires_at, s.ip, s.user_agent
      FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.expires_at > ? ORDER BY s.last_seen_at DESC LIMIT ? OFFSET ?`),
    countSessions: db.prepare('SELECT COUNT(*) AS n FROM sessions WHERE expires_at > ?'),
    // ---- password resets
    insertReset: db.prepare(`INSERT INTO password_resets (token_hash, user_id, created_by, created_at, expires_at)
      VALUES (?, ?, ?, ?, ?)`),
    resetByHash: db.prepare('SELECT * FROM password_resets WHERE token_hash = ?'),
    useReset: db.prepare('UPDATE password_resets SET used_at = ? WHERE id = ? AND used_at IS NULL'),
    dropUserResets: db.prepare('DELETE FROM password_resets WHERE user_id = ? AND used_at IS NULL'),
    setPassword: db.prepare('UPDATE users SET password_hash = ?, failed_logins = 0, locked_until = NULL, updated_at = ? WHERE id = ?'),
    // ---- audit and settings
    insertAudit: db.prepare(`INSERT INTO audit_logs (at, user_id, user_email, action, target, detail, ip)
      VALUES (?, ?, ?, ?, ?, ?, ?)`),
    listAudit: db.prepare('SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?'),
    listAuditByAction: db.prepare('SELECT * FROM audit_logs WHERE action = ? ORDER BY id DESC LIMIT ? OFFSET ?'),
    countAudit: db.prepare('SELECT COUNT(*) AS n FROM audit_logs'),
    pruneAudit: db.prepare('DELETE FROM audit_logs WHERE at < ?'),
    getSetting: db.prepare('SELECT value FROM settings WHERE key = ?'),
    setSetting: db.prepare(`INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`),
  };
  const USER_FIELDS = new Set(['name', 'role', 'status', 'password_hash', 'failed_logins', 'locked_until']);

  return {
    raw: db,
    /** Runs left "running" by a previous gateway process cannot be resumed. */
    markInterruptedRuns: () => {
      stmts.closeOpenTrips.run(now());
      return stmts.markInterrupted.run(now()).changes;
    },
    insertRun: (run) => stmts.insertRun.run({ eco_fraction: 0, ev_routing: 'off', started_by: null, ...run, started_at: now() }),
    endRun: (id, status, message = null) => stmts.endRun.run({ id, status, message, ended_at: now() }).changes,
    saveResult: (runId, metrics) => stmts.upsertResult.run(runId, JSON.stringify(metrics ?? {}), now()),
    saveEvent: (runId, ev) =>
      stmts.insertEvent.run(runId, ev.t ?? null, ev.kind ?? null, ev.tls ?? null, ev.detail ?? null, now()),
    saveSample: (runId, state) => stmts.insertSample.run(runId, state.t ?? null, JSON.stringify(state), now()),
    listRuns: (limit = 50, offset = 0) =>
      stmts.listRuns.all(limit, offset).map((r) => ({ ...r, has_result: Boolean(r.has_result) })),
    getRun: (id, { eventLimit = 500, sampleLimit = 0 } = {}) => {
      const run = stmts.getRun.get(id);
      if (!run) return null;
      const res = stmts.getResult.get(id);
      return {
        run,
        result: res ? { metrics: JSON.parse(res.metrics), created_at: res.created_at } : null,
        events: stmts.getEvents.all(id, eventLimit),
        state_samples: stmts.countSamples.get(id).n,
        samples: sampleLimit > 0 ? stmts.getSamples.all(id, sampleLimit).map((s) => JSON.parse(s.payload)) : undefined,
      };
    },
    /** One-driver mode trips: insert when a viewer takes a car, update when it ends (report from Python). */
    insertDriverTrip: (runId, from, to) => Number(stmts.insertTrip.run(runId, from, to, now()).lastInsertRowid),
    endDriverTrip: (id, { status, reason = null, vid = null, report = null }) =>
      stmts.endTrip.run({ id, status, reason, vid, report: report ? JSON.stringify(report) : null, ended_at: now() }).changes,
    listDriverTrips: (limit = 20) =>
      stmts.listTrips.all(limit).map((r) => ({ ...r, report: r.report ? JSON.parse(r.report) : null })),
    countRuns: () => stmts.countRuns.get().n,
    /** Retention of high-volume telemetry: delete state samples stored before the given time. */
    pruneSamples: (beforeIso) => stmts.pruneSamples.run(beforeIso).changes,
    countSamples: () => stmts.countAllSamples.get().n,
    /** Consistent copy of the whole database into a new file while the gateway keeps running. */
    backup: (file) => db.backup(file),

    // ---- accounts ---------------------------------------------------------
    createUser: ({ email, name, password_hash, role = 'VIEWER', status = 'active' }) =>
      Number(stmts.insertUser.run({ email, name, password_hash, role, status, at: now() }).lastInsertRowid),
    getUserByEmail: (email) => stmts.userByEmail.get(email) ?? null,
    getUserById: (id) => stmts.userById.get(id) ?? null,
    listUsers: (limit = 50, offset = 0) => stmts.listUsers.all(limit, offset),
    countUsers: () => stmts.countUsers.get().n,
    countActiveAdmins: () => stmts.countActiveAdmins.get().n,
    /** Update the given columns of a user (only known column names are accepted). */
    updateUser: (id, fields) => {
      const keys = Object.keys(fields).filter((k) => USER_FIELDS.has(k));
      if (!keys.length) return 0;
      const sql = `UPDATE users SET ${keys.map((k) => `${k} = @${k}`).join(', ')}, updated_at = @updated_at WHERE id = @id`;
      return db.prepare(sql).run({ ...Object.fromEntries(keys.map((k) => [k, fields[k]])), updated_at: now(), id }).changes;
    },
    deleteUser: (id) => stmts.deleteUser.run(id).changes,
    recordLoginFailure: (id, lockedUntil = null) => stmts.loginFail.run({ id, locked_until: lockedUntil }),
    recordLoginSuccess: (id) => stmts.loginOk.run(now(), id),

    // ---- sessions ---------------------------------------------------------
    createSession: ({ token_hash, user_id, csrf, expires_at, ip = null, user_agent = null }) =>
      Number(stmts.insertSession.run({ token_hash, user_id, csrf, expires_at, ip, user_agent, at: now() }).lastInsertRowid),
    getSession: (tokenHash) => stmts.sessionByHash.get(tokenHash) ?? null,
    touchSession: (id) => stmts.touchSession.run(now(), id),
    deleteSession: (id) => stmts.deleteSession.run(id).changes,
    deleteUserSessions: (userId) => stmts.deleteUserSessions.run(userId).changes,
    userSessionIds: (userId) => stmts.userSessionIds.all(userId).map((r) => r.id),
    deleteExpiredSessions: () => stmts.deleteExpiredSessions.run(now()).changes,
    listSessions: (limit = 50, offset = 0) => stmts.listSessions.all(now(), limit, offset),
    countSessions: () => stmts.countSessions.get(now()).n,

    // ---- password resets --------------------------------------------------
    createPasswordReset: (tokenHash, userId, createdBy, expiresAt) => db.transaction(() => {
      stmts.dropUserResets.run(userId); // one open link per user
      stmts.insertReset.run(tokenHash, userId, createdBy, now(), expiresAt);
    })(),
    getPasswordReset: (tokenHash) => stmts.resetByHash.get(tokenHash) ?? null,
    /** Set the new password, consume the link and end all sessions of the user, atomically. */
    completePasswordReset: (resetId, userId, passwordHash) => db.transaction(() => {
      if (!stmts.useReset.run(now(), resetId).changes) return false;
      stmts.setPassword.run(passwordHash, now(), userId);
      stmts.deleteUserSessions.run(userId);
      return true;
    })(),

    // ---- audit and settings -----------------------------------------------
    audit: ({ user = null, action, target = null, detail = null, ip = null }) =>
      stmts.insertAudit.run(now(), user?.id ?? null, user?.email ?? null, action, target,
        detail == null ? null : String(detail).slice(0, 500), ip),
    listAudit: (limit = 50, offset = 0, action = null) =>
      (action ? stmts.listAuditByAction.all(action, limit, offset) : stmts.listAudit.all(limit, offset)),
    countAudit: () => stmts.countAudit.get().n,
    pruneAudit: (beforeIso) => stmts.pruneAudit.run(beforeIso).changes,
    getSetting: (key, fallback = null) => stmts.getSetting.get(key)?.value ?? fallback,
    setSetting: (key, value) => stmts.setSetting.run(key, String(value), now()),
    close: () => db.close(),
  };
}
