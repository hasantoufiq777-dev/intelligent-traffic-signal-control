import { describe, expect, it } from 'vitest';
import { carsCsv, compareGroups, sortCars, summarise } from './ecoScore.js';

const cars = [
  { id: 'a', eco_pct: 90, rating: 'eco', connected: true, eco_car: true, finished: true },
  { id: 'b', eco_pct: 60, rating: 'fair', connected: false, eco_car: false, finished: false },
  { id: 'c', eco_pct: null, rating: null, connected: true, eco_car: false, finished: false },
];

describe('eco score table', () => {
  it('averages only the cars that have a score', () => {
    const s = summarise(cars);
    expect(s).toMatchObject({ total: 3, onRoad: 2, scored: 2, average: 75, connected: 90, notConnected: 60, ecoCars: 90, otherCars: 60 });
    expect(s.ratings).toEqual({ eco: 1, good: 0, fair: 1, wasteful: 0 });
  });

  it('compares eco cars with ordinary cars and leaves heavy vehicles out', () => {
    const g = compareGroups([
      { id: 'e', kind: 'eco_car', eco_car: true, eco_pct: 80, fuel_l_100km: 8, stops: 1, idle_s: 4 },
      { id: 'c', kind: 'car', eco_car: false, eco_pct: 60, fuel_l_100km: 12, stops: 3, idle_s: 20 },
      { id: 'b', kind: 'bus', eco_car: false, eco_pct: 40, fuel_l_100km: 40, stops: 5, idle_s: 50 },
      { id: 'n', kind: 'car', eco_car: false, eco_pct: null, fuel_l_100km: 90 },
    ]);
    expect(g.eco).toMatchObject({ n: 1, eco_pct: 80, fuel_l_100km: 8 });
    expect(g.other).toMatchObject({ n: 1, eco_pct: 60, fuel_l_100km: 12, idle_s: 20 });
  });

  it('has no averages without scored cars', () => {
    expect(summarise([{ id: 'x', eco_pct: null }]).average).toBeNull();
  });

  it('sorts both ways and keeps unscored cars last', () => {
    expect(sortCars(cars, 'eco_pct', 1).map((c) => c.id)).toEqual(['b', 'a', 'c']);
    expect(sortCars(cars, 'eco_pct', -1).map((c) => c.id)).toEqual(['a', 'b', 'c']);
  });

  it('writes one CSV row per car and quotes commas', () => {
    const lines = carsCsv([{ id: 'a,1', eco_pct: 90 }]).trim().split('\n');
    expect(lines).toHaveLength(2);
    expect(lines[0].startsWith('id,kind')).toBe(true);
    expect(lines[1].startsWith('"a,1",')).toBe(true);
  });
});
