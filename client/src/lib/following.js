// Car following (sim/car_following.py): the vehicle ahead, the vehicle behind, and the advice for
// following the one ahead at a safe time gap. Shapes differ between the followed car's detail
// (speeds in m/s) and the driver message (km/h); normalise() makes one shape for the card.
import { isNum } from './format.js';

/** Advice states from the simulation: css class, headline for my car, line for the car behind. */
export const FOLLOW_TEXT = {
  free: { cls: 'go', head: 'Free road: no vehicle within 100 m ahead', behind: 'drives freely' },
  keep: { cls: 'go', head: 'Keep your speed: the gap is right', behind: 'keeps its speed behind you' },
  speed_up: { cls: 'go', head: 'The car ahead is pulling away: you can speed up', behind: 'speeds up after you' },
  slow: { cls: 'slow', head: 'Ease off: you are closing in on the car ahead', behind: 'eases off behind you' },
  too_close: { cls: 'slow', head: 'Too close: drop back to a safe gap', behind: 'is too close and drops back' },
  brake: { cls: 'stop', head: 'Brake: the car ahead is slowing down hard', behind: 'brakes because you slow down' },
  stopped: { cls: 'stop', head: 'The car ahead has stopped: stop behind it', behind: 'stops behind you' },
};

/** What an acceleration (m/s2) means for a driver. */
export function accText(acc) {
  if (!isNum(acc)) return 'unknown';
  if (acc <= -2.5) return `braking hard (${acc.toFixed(1)} m/s²)`;
  if (acc <= -0.3) return `slowing down (${acc.toFixed(1)} m/s²)`;
  if (acc >= 0.3) return `speeding up (+${acc.toFixed(1)} m/s²)`;
  return 'steady speed';
}

/** Time gap (s) = gap / my speed; null when (nearly) stopped. */
export const timeGap = (gapM, speedMs) => (isNum(gapM) && isNum(speedMs) && speedMs > 0.5 ? gapM / speedMs : null);

const fromKmh = (v) => (isNum(v) ? v / 3.6 : null);

/**
 * One shape for the card from either source:
 * { speed, acc, ahead: {id, kind, speed, acc, gap, v2v} | null,
 *   behind: {id, kind, speed, acc, gap, v2v, follow} | null, advice: {state, speed, ttc} | null } (m/s).
 */
export function normalise(src, from = 'detail') {
  if (!src) return null;
  if (from === 'driver') {
    const side = (o) => (o ? { id: o.id, kind: o.kind, speed: fromKmh(o.speed_kmh), acc: o.acc, gap: o.gap_m, v2v: o.v2v,
      follow: o.follow, followSpeed: fromKmh(o.follow_speed_kmh) } : null);
    const f = src.follow;
    return {
      speed: fromKmh(src.speed_kmh), acc: src.acc, ahead: side(src.ahead), behind: side(src.behind),
      advice: f && f.state ? { state: f.state, speed: fromKmh(f.speed_kmh), ttc: f.ttc } : null,
    };
  }
  const side = (o) => (o ? { id: o.id, kind: o.kind, speed: o.speed, acc: o.acc, gap: o.gap, v2v: (Number(o.flags) & 16) !== 0,
    follow: o.follow, followSpeed: o.follow_speed } : null);
  return {
    speed: src.speed, acc: src.acc, ahead: side(src.ahead), behind: side(src.behind),
    advice: src.follow ? { state: src.follow, speed: src.follow_speed, ttc: src.ttc } : (src.ahead === null ? { state: 'free' } : null),
  };
}
