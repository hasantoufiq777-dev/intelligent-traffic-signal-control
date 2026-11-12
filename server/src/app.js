// Builds the gateway: Express REST API + Socket.io live channel + SQLite. Used by index.js and tests.
//
// Access: every /api route except /api/health and /api/auth/* needs a signed-in session. Roles are
// enforced here (never only in the browser): VIEWER watches, RESEARCHER also starts runs, ADMIN
// manages users, settings and the simulation. Live sockets use the same session cookie.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import compression from 'compression';
import cors from 'cors';
import express from 'express';
import helmet from 'helmet';
import { Server as SocketServer } from 'socket.io';
import { createAuth } from './auth.js';
import { loadConfig, makeLogger } from './config.js';
import { openDatabase } from './db.js';
import { LiveChannel } from './liveChannel.js';
import { OPTIONS, PLAYBACK_SPEEDS, PlaybackSpeedSchema, PlaylistSchema, RunEntrySchema } from './protocol.js';

export function loadPlaylist(file, logger) {
  try {
    const parsed = PlaylistSchema.safeParse(JSON.parse(fs.readFileSync(file, 'utf8')));
    if (!parsed.success) {
      logger.warn(`Playlist ${file} is invalid: ${parsed.error.issues.map((i) => i.message).join('; ')}`);
      return [];
    }
    return parsed.data;
  } catch (err) {
    logger.warn(`No playlist loaded from ${file}: ${err.message}`);
    return [];
  }
}

/** The request's host for the CSP, or a harmless placeholder if it holds anything but host characters. */
const cspHost = (req) => {
  const host = req.get('host') ?? '';
  return /^[a-z0-9.\-]+(:\d{1,5})?$|^\[[0-9a-f:.]+\](:\d{1,5})?$/i.test(host) ? host : 'invalid.invalid';
};

export function createGateway(overrides = {}) {
  const cfg = loadConfig(overrides);
  const log = makeLogger(cfg.LOG_LEVEL);
  const db = openDatabase(cfg.DB_PATH);
  const interrupted = db.markInterruptedRuns();
  if (interrupted) log.warn(`Marked ${interrupted} run(s) left "running" as interrupted`);

  const app = express();
  const server = http.createServer(app);
  server.keepAliveTimeout = 65_000;
  server.requestTimeout = 30_000;
  const corsOptions = cfg.ALLOWED_ORIGINS.length ? { origin: cfg.ALLOWED_ORIGINS, credentials: true } : { origin: false };
  const io = new SocketServer(server, {
    cors: corsOptions,
    serveClient: false,
    pingInterval: 25_000,
    pingTimeout: 20_000,
    connectTimeout: 10_000,
    perMessageDeflate: false, // the state stream is small and frequent; compression costs CPU per viewer
    httpCompression: false,
    maxHttpBufferSize: 1e4, // viewers send only small messages (follow, driver commands)
  });

  const auth = createAuth({ cfg, db, log, io });
  io.use(auth.socketMiddleware);

  const playlist = loadPlaylist(cfg.PLAYLIST_PATH, log);
  const live = new LiveChannel({ cfg, io, db, logger: log, playlist });
  live.attach({ accept: auth.onSocketConnected });

  // Event-loop lag: how late a 500 ms timer fires (0 when the loop is idle); last minute
  const lag = { samples: [], last: performance.now() };
  const lagTimer = setInterval(() => {
    const now = performance.now();
    lag.samples.push(Math.max(0, now - lag.last - 500));
    if (lag.samples.length > 120) lag.samples.shift();
    lag.last = now;
  }, 500);
  lagTimer.unref();
  const lagStats = () => {
    const s = [...lag.samples].sort((a, b) => a - b);
    if (!s.length) return { mean: 0, p99: 0, max: 0 };
    const r = (v) => +v.toFixed(1);
    return { mean: r(s.reduce((a, b) => a + b, 0) / s.length), p99: r(s[Math.min(s.length - 1, Math.floor(0.99 * s.length))]), max: r(s.at(-1)) };
  };

  // Housekeeping: telemetry retention (at start-up, then every six hours)
  const pruneTelemetry = () => {
    try {
      const n = db.pruneSamples(new Date(Date.now() - cfg.SAMPLE_RETENTION_DAYS * 86_400_000).toISOString());
      if (n) log.info(`Retention: deleted ${n} state samples older than ${cfg.SAMPLE_RETENTION_DAYS} days`);
    } catch (err) {
      log.warn('Telemetry retention failed:', err.message);
    }
  };
  pruneTelemetry();
  const pruneTimer = setInterval(pruneTelemetry, 6 * 3_600_000);
  pruneTimer.unref();

  app.disable('x-powered-by');
  if (cfg.TRUST_PROXY) app.set('trust proxy', cfg.TRUST_PROXY);
  app.use(helmet({
    contentSecurityPolicy: {
      useDefaults: false,
      directives: {
        defaultSrc: ["'self'"],
        scriptSrc: ["'self'"],
        styleSrc: ["'self'", "'unsafe-inline'"], // React sets element styles (map transforms)
        imgSrc: ["'self'", 'data:'],
        fontSrc: ["'self'", 'data:'],
        // The live socket on this host only ('self' does not cover ws:/wss: in every browser)
        connectSrc: ["'self'", (req) => `wss://${cspHost(req)}`, (req) => `ws://${cspHost(req)}`, ...cfg.ALLOWED_ORIGINS],
        objectSrc: ["'none'"],
        baseUri: ["'self'"],
        formAction: ["'self'"],
        frameAncestors: ["'none'"],
      },
    },
    strictTransportSecurity: cfg.COOKIE_SECURE ? { maxAge: 15_552_000, includeSubDomains: true } : false,
    crossOriginEmbedderPolicy: false,
  }));
  // gzip/deflate for the API and the static build (the live stream is Socket.io, not touched here)
  app.use(compression({ threshold: 1024 }));
  app.use(cors(corsOptions));
  app.use(express.json({ limit: '10kb' }));
  app.use('/api', auth.apiLimiter, auth.authenticate, auth.csrfProtect);

  const { requireAuth, requireRole } = auth;
  const admin = requireRole('ADMIN');
  const page = (req, max = 500) => ({
    limit: Math.min(Math.max(parseInt(req.query.limit, 10) || 50, 1), max),
    offset: Math.max(parseInt(req.query.offset, 10) || 0, 0),
  });
  /** Turn a "busy" lock error into 409; anything else goes to the error handler. */
  const guarded = (fn) => async (req, res, next) => {
    try {
      await fn(req, res);
    } catch (err) {
      if (err?.code === 'busy') return res.status(409).json({ error: err.message, code: 'busy' });
      return next(err);
    }
    return undefined;
  };

  // ---- public -------------------------------------------------------------
  app.get('/api/health', (_req, res) => {
    res.json({ ok: true, uptime_s: Math.round(process.uptime()), mock: cfg.USE_MOCK, live_running: Boolean(live.proc),
      lifecycle: live.lifecycle, viewers: live.viewers(), max_connections: cfg.MAX_CONNECTIONS });
  });

  auth.mount(app); // /api/auth/*, /api/users, /api/sessions, /api/audit, /api/settings

  // ---- signed-in users ------------------------------------------------------
  app.get('/api/live/status', requireAuth, (_req, res) => {
    const np = live.nowPlaying();
    res.json({ running: np.running, lifecycle: np.lifecycle, entry: np.current, next: np.next, loop: np.loop,
      viewers: np.viewers, status: live.lastStatus?.status ?? null });
  });

  app.get('/api/live/playlist', requireAuth, (_req, res) => res.json({ loop: cfg.LIVE_LOOP, playlist: live.playlist }));

  app.get('/api/options', requireAuth, (_req, res) => res.json({ ...OPTIONS, playlist: live.playlist, mock: cfg.USE_MOCK }));

  app.get('/api/experiments/summary', requireAuth, (_req, res) => {
    fs.readFile(cfg.SUMMARY_PATH, 'utf8', (err, text) => {
      if (err) {
        return res.status(404).json({
          error: 'No experiment summary yet. Run: python sim/analytics/evaluate.py --suite all',
          path: path.relative(cfg.PROJECT_ROOT, cfg.SUMMARY_PATH),
        });
      }
      try {
        return res.json(JSON.parse(text));
      } catch {
        return res.status(500).json({ error: 'summary.json is not valid JSON' });
      }
    });
  });

  // Genetic-algorithm tuning output (results/ga/, written by sim/optimizer/ga_tuner.py): the
  // convergence history and the held-out evaluation per target. A target that was not run is null.
  app.get('/api/experiments/ga', requireAuth, async (_req, res) => {
    const dir = path.join(path.dirname(cfg.SUMMARY_PATH), 'ga');
    const read = async (name) => {
      try {
        return JSON.parse(await fs.promises.readFile(path.join(dir, name), 'utf8'));
      } catch {
        return null;
      }
    };
    const out = {};
    for (const target of ['signals', 'aco']) {
      const [history, evaluation] = await Promise.all([read(`ga_${target}_history.json`), read(`ga_${target}_evaluation.json`)]);
      out[target] = history || evaluation ? { history, evaluation } : null;
    }
    res.json(out);
  });

  app.get('/api/runs', requireAuth, (req, res) => {
    const { limit, offset } = page(req);
    res.json({ runs: db.listRuns(limit, offset), total: db.countRuns(), limit, offset });
  });

  app.get('/api/runs/:id', requireAuth, (req, res) => {
    const samples = Math.min(Math.max(parseInt(req.query.samples, 10) || 0, 0), 1000);
    const data = db.getRun(req.params.id, { sampleLimit: samples });
    if (!data) return res.status(404).json({ error: `Run ${req.params.id} not found` });
    return res.json(data);
  });

  // ---- live run control (ADMIN, RESEARCHER) ----------------------------------
  app.post('/api/live/start', requireRole('ADMIN', 'RESEARCHER'), guarded(async (req, res) => {
    const parsed = RunEntrySchema.safeParse(req.body ?? {});
    if (!parsed.success) {
      return res.status(400).json({ error: 'Invalid run options', code: 'invalid',
        issues: parsed.error.issues.map((i) => ({ path: i.path.join('.'), message: i.message })) });
    }
    // A researcher may not replace a run that somebody else started
    const cur = live.current;
    if (req.user.role !== 'ADMIN' && live.proc && cur?.source === 'admin' && cur.started_by?.id !== req.user.id) {
      return res.status(409).json({ error: `A run started by ${cur.started_by?.name ?? 'another user'} is in progress`, code: 'in_use' });
    }
    const runId = await live.exclusive(() => live.start(parsed.data, 'admin', req.user));
    auth.audit(req, 'sim_start', { target: runId, detail: `${parsed.data.scenario} / ${parsed.data.controller} / seed ${parsed.data.seed}` });
    return res.status(202).json({ run_id: runId, entry: parsed.data });
  }));

  app.post('/api/live/stop', requireRole('ADMIN', 'RESEARCHER'), guarded(async (req, res) => {
    const cur = live.current;
    if (req.user.role !== 'ADMIN' && live.proc && cur?.started_by?.id !== req.user.id) {
      auth.audit(req, 'access_denied', { target: 'POST /api/live/stop', detail: 'not the owner of the run' });
      return res.status(403).json({ error: 'Only an administrator can stop a run you did not start', code: 'forbidden' });
    }
    const stopped = await live.exclusive(() => live.stop());
    auth.audit(req, 'sim_stop', { target: cur?.run_id ?? null, detail: stopped ? 'stopped' : 'nothing was running' });
    return res.json({ stopped });
  }));

  // ---- playback: pause, resume, speed, restart (an administrator, or the researcher who started the run)
  const ownsRun = (req, action) => {
    if (req.user.role === 'ADMIN' || live.current?.started_by?.id === req.user.id) return true;
    auth.audit(req, 'access_denied', { target: `POST /api/live/${action}`, detail: 'not the owner of the run' });
    return false;
  };
  const notOwner = (res) => res.status(403).json({ error: 'Only an administrator can control a run you did not start', code: 'forbidden' });
  const control = requireRole('ADMIN', 'RESEARCHER');
  const sendPlayback = (action, makeCmd) => (req, res) => {
    let cmd;
    if (action === 'speed') {
      const parsed = PlaybackSpeedSchema.safeParse(req.body ?? {});
      if (!parsed.success) return res.status(400).json({ error: `Speed must be one of ${PLAYBACK_SPEEDS.join(', ')}`, code: 'invalid' });
      cmd = { cmd: 'speed', speed: parsed.data.speed };
    } else cmd = makeCmd();
    if (!ownsRun(req, action)) return notOwner(res);
    try {
      live.playbackCommand(cmd);
    } catch (err) {
      if (err.code === 'not_running') return res.status(409).json({ error: err.message, code: 'not_running' });
      throw err;
    }
    auth.audit(req, `sim_${action}`, { target: live.current?.run_id ?? null, detail: cmd.speed ? `speed ${cmd.speed}` : null });
    return res.status(202).json({ requested: cmd.cmd, ...(cmd.speed ? { speed: cmd.speed } : {}) });
  };
  app.post('/api/live/pause', control, sendPlayback('pause', () => ({ cmd: 'pause' })));
  app.post('/api/live/resume', control, sendPlayback('resume', () => ({ cmd: 'resume' })));
  app.post('/api/live/speed', control, sendPlayback('speed'));
  app.post('/api/live/restart', control, guarded(async (req, res) => {
    if (!ownsRun(req, 'restart')) return notOwner(res);
    let runId;
    try {
      runId = await live.exclusive(() => live.restart(req.user));
    } catch (err) {
      if (err.code === 'not_running') return res.status(409).json({ error: err.message, code: 'not_running' });
      throw err;
    }
    auth.audit(req, 'sim_restart', { target: runId });
    return res.status(202).json({ run_id: runId, entry: live.current?.entry });
  }));

  // ---- SUMO-GUI window (opens on the computer running the gateway; ADMIN) -------
  app.get('/api/sumo/gui', requireAuth, (req, res) => {
    res.json({ available: !cfg.USE_MOCK && req.user.role === 'ADMIN', enabled: live.gui, mock: cfg.USE_MOCK,
      running: Boolean(live.proc) });
  });

  app.post('/api/sumo/gui', admin, guarded(async (req, res) => {
    if (cfg.USE_MOCK) return res.status(409).json({ error: 'SUMO-GUI is not available with the mock simulation (USE_MOCK=true)' });
    if (typeof req.body?.enabled !== 'boolean') return res.status(400).json({ error: 'Body must be {"enabled": true|false}' });
    const out = await live.exclusive(() => live.setGui(req.body.enabled));
    auth.audit(req, 'gui_toggle', { detail: `enabled=${req.body.enabled}` });
    return res.json(out);
  }));

  // ---- one-driver mode -----------------------------------------------------
  // Signed-in users take the seat and drive over Socket.io (driver.js); these endpoints manage it.
  app.get('/api/driver', requireAuth, (_req, res) => {
    res.json({ ...live.driver.public(), trips: db.listDriverTrips(20) });
  });

  app.post('/api/driver', admin, (req, res) => {
    if (typeof req.body?.enabled !== 'boolean') return res.status(400).json({ error: 'Body must be {"enabled": true|false}' });
    auth.audit(req, 'driver_toggle', { detail: `enabled=${req.body.enabled}` });
    return res.json(live.driver.setEnabled(req.body.enabled));
  });

  app.post('/api/driver/release', admin, (req, res) => {
    const released = live.driver.end('admin');
    auth.audit(req, 'driver_release', { detail: released ? 'drive ended' : 'nobody was driving' });
    res.json({ released });
  });

  // ---- administration: server load and health --------------------------------
  const systemInfo = () => {
    const mem = process.memoryUsage();
    return {
      viewers: live.viewers(), max_connections: cfg.MAX_CONNECTIONS, ...auth.stats(),
      cpu: process.cpuUsage(), uptime_s: process.uptime(),
      memory_mb: { rss: +(mem.rss / 1e6).toFixed(1), heapUsed: +(mem.heapUsed / 1e6).toFixed(1) },
      event_loop_ms: lagStats(),
      live: live.stats,
    };
  };

  app.get('/api/metrics', admin, (_req, res) => res.json(systemInfo()));

  app.get('/api/system', admin, (_req, res) => {
    let database = { ok: true };
    try {
      database = { ok: true, users: db.countUsers(), sessions: db.countSessions(), runs: db.countRuns(), audit_entries: db.countAudit(),
        state_samples: db.countSamples(), sample_retention_days: cfg.SAMPLE_RETENTION_DAYS,
        migrations: db.raw.prepare('SELECT version, name, applied_at FROM schema_migrations ORDER BY version').all() };
    } catch (err) {
      log.error('Database health check failed:', err.message);
      database = { ok: false, error: 'Database is not available' };
    }
    res.status(database.ok ? 200 : 503).json({
      ...systemInfo(), database,
      simulation: { lifecycle: live.lifecycle, failures: live.failures, max_failures: cfg.MAX_SIM_FAILURES, mock: cfg.USE_MOCK,
        loop: cfg.LIVE_LOOP && !live.paused, current: live.current, last_status: live.lastStatus },
      node: process.version, env: cfg.NODE_ENV,
    });
  });

  // ---- optional static React build (production: nginx serves it, see README) --
  const dist = path.join(cfg.PROJECT_ROOT, 'client', 'dist');
  if (fs.existsSync(path.join(dist, 'index.html'))) {
    // Vite puts a content hash in every file name under assets/, so those never change: cache for a year
    app.use('/assets', express.static(path.join(dist, 'assets'), { immutable: true, maxAge: '365d', index: false, fallthrough: false }));
    app.use(express.static(dist, { maxAge: '1h', index: false }));
    app.get(/^\/(?!api\/|socket\.io\/).*/, (_req, res) => {
      res.set('Cache-Control', 'no-cache');
      res.sendFile(path.join(dist, 'index.html'));
    });
  }

  app.use('/api', (_req, res) => res.status(404).json({ error: 'Not found', code: 'not_found' }));
  // Central error handler: details go to the log, never to the client
  // eslint-disable-next-line no-unused-vars
  app.use((err, _req, res, _next) => {
    if (err?.type === 'entity.parse.failed') return res.status(400).json({ error: 'Body is not valid JSON', code: 'invalid' });
    if (err?.type === 'entity.too.large') return res.status(413).json({ error: 'Body too large', code: 'invalid' });
    log.error('Unhandled error:', err);
    return res.status(500).json({ error: 'Internal server error', code: 'server_error' });
  });

  let closed = false;
  async function close() {
    if (closed) return;
    closed = true;
    auth.close();
    clearInterval(lagTimer);
    clearInterval(pruneTimer);
    await live.close();
    await new Promise((resolve) => io.close(() => resolve()));
    if (server.listening) await new Promise((resolve) => server.close(() => resolve()));
    db.close();
  }

  function listen(port = cfg.PORT, host = cfg.HOST) {
    return new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(port, host, () => resolve(server.address()));
    });
  }

  return { cfg, log, app, server, io, db, live, auth, listen, close };
}
