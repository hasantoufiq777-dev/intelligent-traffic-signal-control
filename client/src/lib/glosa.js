// GLOSA advice rule, mirroring sim/vanet/glosa.py (glosa_advice). Used to show suggested advice for a
// followed car that is not an eco car (eco cars carry the simulation's own advice). Pure function.
export const GLOSA_DEFAULTS = { vMin: 5.0, marginS: 1.0, maxUncertaintyS: 5.0 };

/**
 * @param {number} distM distance to the stop line along the route (m)
 * @param {object|null} spat {signal, min_s, max_s, next_green_min_s, next_green_max_s, preempted?}
 * @param {number} vMax the car's normal speed (m/s)
 * @returns {{advice: 'go'|'slow'|'stop'|'none', speed: number|null, greenIn: number|null, greenEndsIn: number|null, certain: boolean}}
 */
export function glosaAdvice(distM, spat, vMax, opts = {}) {
  const { vMin, marginS, maxUncertaintyS } = { ...GLOSA_DEFAULTS, ...opts };
  const out = { advice: 'none', speed: null, greenIn: null, greenEndsIn: null, certain: false };
  if (!spat || spat.preempted || !(distM > 0) || !(vMax > 0.1)) return out;
  const tFree = distM / vMax;
  if (spat.signal === 'G') {
    const ends = spat.min_s;
    out.greenEndsIn = ends == null ? null : Math.max(0, ends);
    if (ends != null && tFree <= ends - marginS) return { ...out, advice: 'go', speed: vMax, certain: true };
  }
  const gMin = spat.next_green_min_s;
  const gMax = spat.next_green_max_s;
  if (gMin == null) return out;
  const target = Math.max(0, gMin);
  out.greenIn = target;
  if (tFree >= target + marginS) return { ...out, advice: 'go', speed: vMax };
  const certain = gMax != null && gMax - target <= maxUncertaintyS;
  out.certain = certain;
  if (!certain) return out;
  const v = distM / (target + marginS);
  if (v >= vMin) return { ...out, advice: 'slow', speed: Math.min(v, vMax) };
  return { ...out, advice: 'stop' };
}

export const ADVICE_TEXT = {
  go: 'Keep going',
  slow: 'Ease off',
  stop: 'Prepare to stop',
  none: 'No advice',
};
