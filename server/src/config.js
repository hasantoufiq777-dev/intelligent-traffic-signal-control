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
  ADMIN_TOKEN: z.string().default(''),
  LIVE_LOOP: bool.default(false),
  PLAYLIST_PATH: z.string().default(path.join(SERVER_ROOT, 'playlist.json')),
  DB_PATH: z.string().default(path.join(SERVER_ROOT, 'data', 'gateway.db')),
  SUMMARY_PATH: z.string().optional(),
  RESULTS_DIR: z.string().optional(),
  CORS_ORIGIN: z.string().default('*'),
  // Real-time pacing of the live simulation (wall-clock seconds per simulated second)
  SIM_STEP_DELAY: z.coerce.number().min(0).default(0.25),
  SIM_MAX_STEPS: z.coerce.number().int().positive().optional(),
  MOCK_STEP_DELAY: z.coerce.number().min(0).default(0.5),
  MOCK_MAX_STEPS: z.coerce.number().int().positive().default(300),
  STATE_INTERVAL_MS: z.coerce.number().int().positive().default(1000),
  HISTORY_SECONDS: z.coerce.number().int().positive().default(120),
  EVENT_HISTORY: z.coerce.number().int().positive().default(200),
  SAMPLE_EVERY_SIM_S: z.coerce.number().positive().default(2), // persist one state per N simulated seconds
  VIEWER_BROADCAST_MS: z.coerce.number().int().positive().default(5000),
  CRASH_RETRY_MS: z.coerce.number().int().min(0).default(5000),
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
