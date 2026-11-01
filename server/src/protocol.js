// Allowed run options (mirror sim/main.py) and parsing of the Python JSON-lines protocol.
import { z } from 'zod';

export const OPTIONS = {
  scenarios: ['normal', 'congested', 'emergency', 'incident', 'navigation'],
  controllers: ['fixed', 'adaptive', 'adaptive_vanet', 'adaptive_ev', 'adaptive_ev_vanet', 'adaptive_ev_vanet_tuned'],
  nav: ['none', 'astar', 'aco', 'aco_tuned'],
  relay: ['on', 'off'],
  defaults: { scenario: 'normal', controller: 'adaptive_vanet', nav: 'none', seed: 1, penetration: 1.0, loss: 0.0, relay: 'off' },
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
    label: z.string().max(120).optional(),
  })
  .strict();

export const PlaylistSchema = z.array(RunEntrySchema).min(1);

const MessageSchema = z.object({ type: z.enum(['status', 'state', 'event', 'result']) }).passthrough();

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
export function buildSimArgs(entry, runId, cfg) {
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
    '--live',
  ];
  if (cfg.USE_MOCK) {
    args.push('--step-delay', String(cfg.MOCK_STEP_DELAY), '--max-steps', String(cfg.MOCK_MAX_STEPS));
  } else {
    if (cfg.SIM_STEP_DELAY > 0) args.push('--step-delay', String(cfg.SIM_STEP_DELAY));
    if (cfg.SIM_MAX_STEPS) args.push('--max-steps', String(cfg.SIM_MAX_STEPS));
  }
  return args;
}
