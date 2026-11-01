// Builds the gateway: Express REST API + Socket.io live channel + SQLite. Used by index.js and tests.
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import cors from 'cors';
import express from 'express';
import { Server as SocketServer } from 'socket.io';
import { loadConfig, makeLogger } from './config.js';
import { openDatabase } from './db.js';
import { LiveChannel } from './liveChannel.js';
import { OPTIONS, PlaylistSchema, RunEntrySchema } from './protocol.js';

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

function tokenMatches(given, expected) {
  const a = Buffer.from(String(given ?? ''));
  const b = Buffer.from(String(expected));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

export function createGateway(overrides = {}) {
  const cfg = loadConfig(overrides);
  const log = makeLogger(cfg.LOG_LEVEL);
  const db = openDatabase(cfg.DB_PATH);
  const interrupted = db.markInterruptedRuns();
  if (interrupted) log.warn(`Marked ${interrupted} run(s) left "running" as interrupted`);

  const app = express();
  const server = http.createServer(app);
  server.keepAliveTimeout = 65_000;
  const io = new SocketServer(server, {
    cors: { origin: cfg.CORS_ORIGIN === '*' ? true : cfg.CORS_ORIGIN.split(',').map((s) => s.trim()) },
    serveClient: false,
    pingInterval: 25_000,
    pingTimeout: 20_000,
    connectTimeout: 10_000,
    perMessageDeflate: false, // the state stream is small and frequent; compression costs CPU per viewer
    httpCompression: false,
    maxHttpBufferSize: 1e4, // viewers send nothing of size
  });

  const playlist = loadPlaylist(cfg.PLAYLIST_PATH, log);
  const live = new LiveChannel({ cfg, io, db, logger: log, playlist });
  live.attach();

  app.disable('x-powered-by');
  app.use(cors({ origin: cfg.CORS_ORIGIN === '*' ? true : cfg.CORS_ORIGIN.split(',').map((s) => s.trim()) }));
  app.use(express.json({ limit: '10kb' }));

  const requireAdmin = (req, res, next) => {
    if (!cfg.ADMIN_TOKEN) return res.status(503).json({ error: 'ADMIN_TOKEN is not configured on the server' });
    if (!tokenMatches(req.get('X-Admin-Token'), cfg.ADMIN_TOKEN)) {
      return res.status(401).json({ error: 'Missing or invalid X-Admin-Token' });
    }
    return next();
  };

  // ---- public -------------------------------------------------------------
  app.get('/api/health', (_req, res) => {
    res.json({ ok: true, uptime_s: Math.round(process.uptime()), mock: cfg.USE_MOCK, live_running: Boolean(live.proc),
      viewers: live.viewers() });
  });

  app.get('/api/live/status', (_req, res) => {
    const np = live.nowPlaying();
    res.json({ running: np.running, entry: np.current, next: np.next, loop: np.loop, viewers: np.viewers,
      status: live.lastStatus?.status ?? null });
  });

  app.get('/api/live/playlist', (_req, res) => res.json({ loop: cfg.LIVE_LOOP, playlist: live.playlist }));

  app.get('/api/options', (_req, res) => res.json({ ...OPTIONS, playlist: live.playlist, mock: cfg.USE_MOCK }));

  app.get('/api/experiments/summary', (_req, res) => {
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

  app.get('/api/runs', (req, res) => {
    const limit = Math.min(Math.max(parseInt(req.query.limit, 10) || 50, 1), 500);
    const offset = Math.max(parseInt(req.query.offset, 10) || 0, 0);
    res.json({ runs: db.listRuns(limit, offset), limit, offset });
  });

  app.get('/api/runs/:id', (req, res) => {
    const samples = Math.min(Math.max(parseInt(req.query.samples, 10) || 0, 0), 1000);
    const data = db.getRun(req.params.id, { sampleLimit: samples });
    if (!data) return res.status(404).json({ error: `Run ${req.params.id} not found` });
    return res.json(data);
  });

  // Server load figures (used by the load test)
  app.get('/api/metrics', (_req, res) => {
    const mem = process.memoryUsage();
    res.json({ viewers: live.viewers(), cpu: process.cpuUsage(), uptime_s: process.uptime(),
      memory_mb: { rss: +(mem.rss / 1e6).toFixed(1), heapUsed: +(mem.heapUsed / 1e6).toFixed(1) }, live: live.stats });
  });

  // ---- admin --------------------------------------------------------------
  app.post('/api/live/start', requireAdmin, async (req, res) => {
    const parsed = RunEntrySchema.safeParse(req.body ?? {});
    if (!parsed.success) {
      return res.status(400).json({ error: 'Invalid run options',
        issues: parsed.error.issues.map((i) => ({ path: i.path.join('.'), message: i.message })) });
    }
    const runId = await live.start(parsed.data, 'admin');
    return res.status(202).json({ run_id: runId, entry: parsed.data });
  });

  app.post('/api/live/stop', requireAdmin, async (_req, res) => {
    const stopped = await live.stop();
    res.json({ stopped });
  });

  // ---- optional static React build (production: nginx serves it, see README) --
  const dist = path.join(cfg.PROJECT_ROOT, 'client', 'dist');
  if (fs.existsSync(path.join(dist, 'index.html'))) {
    app.use(express.static(dist, { maxAge: '1h', index: false }));
    app.get(/^\/(?!api\/|socket\.io\/).*/, (_req, res) => res.sendFile(path.join(dist, 'index.html')));
  }

  app.use('/api', (_req, res) => res.status(404).json({ error: 'Not found' }));
  // eslint-disable-next-line no-unused-vars
  app.use((err, _req, res, _next) => {
    if (err?.type === 'entity.parse.failed') return res.status(400).json({ error: 'Body is not valid JSON' });
    if (err?.type === 'entity.too.large') return res.status(413).json({ error: 'Body too large' });
    log.error('Unhandled error:', err);
    return res.status(500).json({ error: 'Internal server error' });
  });

  let closed = false;
  async function close() {
    if (closed) return;
    closed = true;
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

  return { cfg, log, app, server, io, db, live, listen, close };
}
