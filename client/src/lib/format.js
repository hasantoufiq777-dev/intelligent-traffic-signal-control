export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

export function fmt(v, digits = 1, fallback = '–') {
  return isNum(v) ? v.toFixed(digits) : fallback;
}

export const kmh = (ms) => (isNum(ms) ? Math.round(ms * 3.6) : null);

export function fmtPct(v, digits = 1) {
  return isNum(v) ? `${v > 0 ? '+' : ''}${v.toFixed(digits)}%` : '–';
}

export function fmtP(p) {
  if (!isNum(p)) return '–';
  return p < 0.001 ? '<0.001' : p.toFixed(3);
}

export function seconds(v) {
  if (!isNum(v)) return '–';
  const s = Math.max(0, Math.round(v));
  return s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`;
}

export const SIGNAL_LABEL = { G: 'Green', y: 'Yellow', r: 'Red' };
