// SQLite persistence: runs, final results, events, and state samples (one per SAMPLE_INTERVAL_MS).
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
`;

const now = () => new Date().toISOString();

export function openDatabase(dbPath) {
  if (dbPath !== ':memory:') fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  const db = new Database(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  db.exec(SCHEMA);

  const stmts = {
    insertRun: db.prepare(`INSERT INTO runs (id, scenario, controller, nav, seed, penetration, loss, relay, source, status, started_at)
      VALUES (@id, @scenario, @controller, @nav, @seed, @penetration, @loss, @relay, @source, 'running', @started_at)`),
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
  };

  return {
    raw: db,
    /** Runs left "running" by a previous gateway process cannot be resumed. */
    markInterruptedRuns: () => stmts.markInterrupted.run(now()).changes,
    insertRun: (run) => stmts.insertRun.run({ ...run, started_at: now() }),
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
    close: () => db.close(),
  };
}
