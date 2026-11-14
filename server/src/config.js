// Gateway configuration from environment variables (.env supported).
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';
import { z } from 'zod';

const here = path.dirname(fileURLToPath(import.meta.url));
export const SERVER_ROOT = path.resolve(here, '..');

const bool = z
  .union([z.boolean(), z.string()])
  .transform((v) => (typeof v === 'boolean' ? v : ['1', 'true', 'yes', 'on'].includes(v.toLowerCase())));

const EnvSchema = z.object({
  PORT: z.coerce.number().int().min(0).max(65535).default(4000),
  HOST: z.string().default('0.0.0.0'),
  PROJECT_ROOT: z.string().default(path.resolve(SERVER_ROOT, '..')),
  PYTHON_BIN: z.string().default(process.platform === 'win32' ? 'python' : 'python3'),
  SIM_ENTRY: z.string().default('sim/main.py'),
  MOCK_ENTRY: z.string().default('sim/mock_sim.py'),
  USE_MOCK: bool.default(false),
  // ---- accounts and security -------------------------------------------------
  NODE_ENV: z.string().default('development'),
  // First administrator, created at start-up when no admin account exists (or use: npm run create-admin)
  ADMIN_EMAIL: z.string().optional(),
  ADMIN_PASSWORD: z.string().optional(),
  ALLOW_REGISTRATION: bool.default(false), // initial value; admins change it at run time (settings)
  SESSION_TTL_HOURS: z.coerce.number().positive().default(12),
  SESSIONS_PER_USER: z.coerce.number().int().positive().default(10),
  COOKIE_SECURE: bool.optional(), // default: true when NODE_ENV=production (HTTPS only)
  TRUST_PROXY: z.coerce.number().int().min(0).default(0), // number of reverse proxies in front (nginx: 1)
  LOGIN_MAX_FAILS: z.coerce.number().int().positive().default(5),
  LOGIN_LOCK_MINUTES: z.coerce.number().positive().default(15),
  RESET_LINK_MINUTES: z.coerce.number().positive().default(60),
  RATE_LIMIT_PER_MIN: z.coerce.number().int().positive().default(600), // API requests per IP per minute
  AUTH_RATE_LIMIT: z.coerce.number().int().positive().default(30), // login/register/reset attempts per IP per 15 min
  AUDIT_RETENTION_DAYS: z.coerce.number().positive().default(180),
  // State samples (one per SAMPLE_EVERY_SIM_S per run) are the bulky telemetry: deleted after this
  // many days. Runs, their results and their events are kept.
  SAMPLE_RETENTION_DAYS: z.coerce.number().positive().default(14),
  // Hard cap on concurrent live connections (Socket.io); further connections are refused with "capacity"
  MAX_CONNECTIONS: z.coerce.number().int().positive().default(1000),
  // Consecutive failed runs after which the playlist stops retrying (lifecycle FAILED)
  MAX_SIM_FAILURES: z.coerce.number().int().positive().default(3),
  LIVE_LOOP: bool.default(false),
  PLAYLIST_PATH: z.string().default(path.join(SERVER_ROOT, 'playlist.json')),
  DB_PATH: z.string().default(path.join(SERVER_ROOT, 'data', 'gateway.db')),
  SUMMARY_PATH: z.string().optional(),
  RESULTS_DIR: z.string().optional(),
  // Extra browser origins allowed to call the API with cookies (comma separated). Empty = same origin only.
  CORS_ORIGIN: z.string().default(''),
  // Real-time pacing of the live simulation (wall-clock seconds per simulated second)
  SIM_STEP_DELAY: z.coerce.number().min(0).default(0.25),
  SIM_MAX_STEPS: z.coerce.number().int().positive().optional(),
  // Open the live run in the SUMO-GUI window too (only useful when the gateway runs on a desktop)
  SIM_GUI: bool.default(false),
  MOCK_STEP_DELAY: z.coerce.number().min(0).default(0.5),
  MOCK_MAX_STEPS: z.coerce.number().int().positive().default(300),
  STATE_INTERVAL_MS: z.coerce.number().int().positive().default(1000),
  HISTORY_SECONDS: z.coerce.number().int().positive().default(120),
  EVENT_HISTORY: z.coerce.number().int().positive().default(200),
  SAMPLE_EVERY_SIM_S: z.coerce.number().positive().default(2), // persist one state per N simulated seconds
  VIEWER_BROADCAST_MS: z.coerce.number().int().positive().default(5000),
  CRASH_RETRY_MS: z.coerce.number().int().min(0).default(5000),
  // One-driver mode: one viewer at a time drives a car in the live run (the admin can switch it off)
  DRIVER_MODE: bool.default(true),
  DRIVER_IDLE_MS: z.coerce.number().int().positive().default(120_000), // no input this long: car handed back
  DRIVER_MAX_MS: z.coerce.number().int().positive().default(600_000), // longest drive
  DRIVER_RESUME_MS: z.coerce.number().int().min(0).default(15_000), // reconnect grace for the driver
  DRIVER_CMDS_PER_S: z.coerce.number().positive().default(2), // command rate limit (burst: 2 x this)
  DRIVER_STEP_DELAY: z.coerce.number().min(0).default(0.5), // live pacing while someone drives (real SUMO)
  LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error', 'silent']).default('info'),
});

/** Load and validate configuration. `overrides` (used by tests) win over the environment. */
export function loadConfig(overrides = {}) {
  dotenv.config({ path: path.join(SERVER_ROOT, '.env'), quiet: true });
  const parsed = EnvSchema.safeParse({ ...process.env, ...overrides });
  if (!parsed.success) {
    const msg = parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; ');
    throw new Error(`Invalid gateway configuration: ${msg}`);
  }
  const cfg = parsed.data;
  cfg.SUMMARY_PATH ??= path.join(cfg.PROJECT_ROOT, 'results', 'summary.json');
  cfg.COOKIE_SECURE ??= cfg.NODE_ENV === 'production';
  cfg.ALLOWED_ORIGINS = cfg.CORS_ORIGIN.split(',').map((o) => o.trim().replace(/\/$/, '')).filter((o) => o && o !== '*');
  // A relative interpreter path (e.g. ../.venv/Scripts/python.exe) is relative to server/, where .env lives,
  // not to the project root the simulation runs in. A bare command name (python, python3) stays on PATH.
  if (/[\\/]/.test(cfg.PYTHON_BIN) && !path.isAbsolute(cfg.PYTHON_BIN)) {
    cfg.PYTHON_BIN = path.resolve(SERVER_ROOT, cfg.PYTHON_BIN);
  }
  return cfg;
}

/** Minimal leveled logger (stderr), so stdout stays quiet. */
export function makeLogger(level = 'info') {
  const order = { debug: 10, info: 20, warn: 30, error: 40, silent: 100 };
  const min = order[level] ?? 20;
  const log = (lvl) => (...args) => {
    if (order[lvl] >= min) console.error(new Date().toISOString(), `[${lvl}]`, ...args);
  };
  return { debug: log('debug'), info: log('info'), warn: log('warn'), error: log('error') };
}
