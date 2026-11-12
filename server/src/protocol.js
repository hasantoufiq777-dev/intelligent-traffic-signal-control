// Allowed run options (mirror sim/main.py) and parsing of the Python JSON-lines protocol.
import { z } from 'zod';

export const OPTIONS = {
  scenarios: ['normal', 'congested', 'emergency', 'incident', 'navigation', 'ev_incident', 'ev_congested', 'accident_zone'],
  controllers: ['fixed', 'adaptive', 'adaptive_vanet', 'adaptive_ev', 'adaptive_ev_vanet', 'adaptive_ev_vanet_tuned'],
  nav: ['none', 'astar', 'aco', 'aco_tuned'],
  relay: ['on', 'off'],
  ev_routing: ['on', 'off'],
  networks: ['single', 'grid2x2', 'grid2x2_zone'],
  defaults: { scenario: 'normal', controller: 'adaptive_vanet', nav: 'none', seed: 1, penetration: 1.0, loss: 0.0, relay: 'off',
    eco_fraction: 0.0, ev_routing: 'off', demand: 1.0 },
  // Traffic demand factor: every flow of the scenario multiplied by this (vehicle count)
  demand: { min: 0.25, max: 2.0, step: 0.25 },
};

/** A live run request (admin start body, playlist entry). Unknown keys are rejected. */
export const RunEntrySchema = z
  .object({
    scenario: z.enum(OPTIONS.scenarios),
    controller: z.enum(OPTIONS.controllers),
    nav: z.enum(OPTIONS.nav).default('none'),
    seed: z.coerce.number().int().min(0).max(1_000_000).default(1),
    penetration: z.coerce.number().min(0).max(1).default(1.0),
    loss: z.coerce.number().min(0).max(1).default(0.0),
    relay: z.enum(OPTIONS.relay).default('off'),
    eco_fraction: z.coerce.number().min(0).max(1).default(0.0), // share of equipped cars following GLOSA advice
    ev_routing: z.enum(OPTIONS.ev_routing).default('off'), // ambulances pick their route on the TMC map
    demand: z.coerce.number().min(0.25).max(2).default(1.0), // traffic demand factor (number of cars)
    network: z.enum(OPTIONS.networks).optional(), // default: the scenario's own network
    label: z.string().max(120).optional(),
  })
  .strict();

export const PlaylistSchema = z.array(RunEntrySchema).min(1);

const MessageSchema = z.object({ type: z.enum(['status', 'state', 'event', 'result', 'network', 'vehicles', 'driver', 'playback']) }).passthrough();

/** Road ids a driver may pick (normal edges: no ":" internal junction lanes). */
const EdgeIdSchema = z.string().min(1).max(64).regex(/^[\w.#-]+$/);

/** One-driver mode: take a car from one road to another. */
export const DriverStartSchema = z.object({ from: EdgeIdSchema, to: EdgeIdSchema }).strict()
  .refine((b) => b.from !== b.to, { message: 'Start and destination must differ', path: ['to'] });

/** One-driver mode: a driving command. speed_kmh only for "ease" (hold at most this speed). */
export const DriverCommandSchema = z.object({
  action: z.enum(['go', 'ease', 'stop']),
  speed_kmh: z.coerce.number().min(5).max(60).optional(),
}).strict();

/** Vehicle ids a viewer may follow (also used as a Socket.io room name suffix). */
// Live playback speed, relative to the run's normal pacing (the simulation accepts the same values)
export const PLAYBACK_SPEEDS = [0.5, 1, 2, 4];
export const PlaybackSpeedSchema = z.object({ speed: z.coerce.number().refine((v) => PLAYBACK_SPEEDS.includes(v), 'Speed must be 0.5, 1, 2 or 4') }).strict();

export const VehicleIdSchema = z.string().min(1).max(64).regex(/^[\w.:#-]+$/);

/**
 * Parse one stdout line. Returns the message object, or null for blank/invalid lines.
 * Events without "kind" (older internal shape with "event") are normalised.
 */
export function parseLine(line) {
  const text = String(line ?? '').trim();
  if (!text) return null;
  let obj;
  try {
    obj = JSON.parse(text);
  } catch {
    return null;
  }
  const res = MessageSchema.safeParse(obj);
  if (!res.success) return null;
  const msg = res.data;
  if (msg.type === 'event') {
    msg.kind ??= msg.event ?? 'unknown';
    msg.tls ??= msg.tls_id ?? '';
    msg.detail ??= '';
  }
  return msg;
}

/** Command-line arguments for sim/main.py or sim/mock_sim.py (args array, never a shell string). */
export function buildSimArgs(entry, runId, cfg, opts = {}) {
  const gui = opts.gui ?? cfg.SIM_GUI;
  const args = [
    cfg.USE_MOCK ? cfg.MOCK_ENTRY : cfg.SIM_ENTRY,
    '--run-id', runId,
    '--scenario', entry.scenario,
    '--controller', entry.controller,
    '--nav', entry.nav,
    '--seed', String(entry.seed),
    '--penetration', String(entry.penetration),
    '--loss', String(entry.loss),
    '--relay', entry.relay,
    '--eco-fraction', String(entry.eco_fraction ?? 0),
    '--ev-routing', entry.ev_routing ?? 'off',
    '--live',
    '--driver', // read commands on stdin: one-driver mode and playback (pause, resume, speed)
  ];
  if (entry.network) args.push('--network', entry.network);
  if (entry.demand != null && entry.demand !== 1) args.push('--demand', String(entry.demand));
  if (cfg.USE_MOCK) {
    args.push('--step-delay', String(cfg.MOCK_STEP_DELAY), '--max-steps', String(cfg.MOCK_MAX_STEPS));
  } else {
    if (cfg.SIM_STEP_DELAY > 0) args.push('--step-delay', String(cfg.SIM_STEP_DELAY));
    if (cfg.SIM_MAX_STEPS) args.push('--max-steps', String(cfg.SIM_MAX_STEPS));
    if (gui) args.push('--gui');
  }
  return args;
}
