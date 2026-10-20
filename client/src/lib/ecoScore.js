// Per-car eco score against the HBEFA4 reference (computed in sim/eco_selection.py): summary, sorting, CSV.
import { isNum } from './format.js';

export const RATING = {
  eco: { label: 'Eco', range: '85–100 %', cls: 'good' },
  good: { label: 'Good', range: '70–85 %', cls: 'good' },
  fair: { label: 'Fair', range: '55–70 %', cls: '' },
  wasteful: { label: 'Wasteful', range: 'below 55 %', cls: 'bad' },
};

// Average eco score of a list of scored cars.
const mean = (rows) => (rows.length ? rows.reduce((a, r) => a + r.eco_pct, 0) / rows.length : null);

// Totals for the Driving page tiles: average score, ratings, radio/no radio, eco/other cars.
/** Average eco % (each scored car counts once), split by rating, radio and eco-car status. */
export function summarise(cars) {
  const scored = cars.filter((c) => isNum(c.eco_pct));
  const ratings = { eco: 0, good: 0, fair: 0, wasteful: 0 };
  for (const c of scored) if (c.rating in ratings) ratings[c.rating] += 1;
  return {
    total: cars.length,
    onRoad: cars.filter((c) => !c.finished).length,
    scored: scored.length,
    average: mean(scored),
    connected: mean(scored.filter((c) => c.connected)),
    notConnected: mean(scored.filter((c) => !c.connected)),
    ecoCars: mean(scored.filter((c) => c.eco_car)),
    otherCars: mean(scored.filter((c) => !c.eco_car)),
    nEcoCars: scored.filter((c) => c.eco_car).length,
    ratings,
  };
}

// Eco cars against ordinary cars. Only passenger cars count, because buses and trucks burn far more.
export function compareGroups(cars) {
  const scored = cars.filter((c) => isNum(c.eco_pct) && (c.kind === 'car' || c.kind === 'eco_car'));
  const average = (rows, key) => {
    const vals = rows.map((r) => r[key]).filter(isNum);
    return vals.length ? vals.reduce((a, v) => a + v, 0) / vals.length : null;
  };
  const group = (rows) => ({
    n: rows.length,
    ...Object.fromEntries(['eco_pct', 'fuel_l_100km', 'co2_g_km', 'stops', 'idle_s'].map((k) => [k, average(rows, k)])),
  });
  return { eco: group(scored.filter((c) => c.eco_car)), other: group(scored.filter((c) => !c.eco_car)) };
}

// Sort cars by a column; cars without a value stay last.
/** Sort a copy by a numeric or text key; cars without a value always go last. */
export function sortCars(cars, key, dir = 1) {
  const missing = (v) => v === null || v === undefined;
  return [...cars].sort((a, b) => {
    const x = a[key];
    const y = b[key];
    if (missing(x) || missing(y)) return missing(x) - missing(y);
    const d = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y));
    return d * dir || String(a.id).localeCompare(String(b.id));
  });
}

const COLUMNS = ['id', 'kind', 'emission_class', 'finished', 'connected', 'eco_car', 'eco_pct', 'rating', 'fuel_l_100km',
  'model_fuel_l_100km', 'co2_g_km', 'model_co2_g_km', 'excess_pct', 'avg_kmh', 'best_kmh', 'dist_m', 'travel_s', 'stops', 'idle_s'];

// Build the CSV download with one row per car.
export function carsCsv(cars) {
  const cell = (v) => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return `${[COLUMNS.join(','), ...cars.map((c) => COLUMNS.map((k) => cell(c[k])).join(','))].join('\n')}\n`;
}
