// Same cases as sim/tests/test_eco.py, so the dashboard's suggested advice matches the simulation's rule.
import { describe, expect, it } from 'vitest';
import { glosaAdvice } from './glosa.js';

const EXACT_RED = { signal: 'r', min_s: 20, max_s: 20, next_green_min_s: 20, next_green_max_s: 20 };

describe('glosaAdvice', () => {
  it('goes when the green surely lasts', () => {
    const g = { signal: 'G', min_s: 15, max_s: 15, next_green_min_s: 50, next_green_max_s: 50 };
    expect(glosaAdvice(100, g, 13)).toMatchObject({ advice: 'go', speed: 13, greenEndsIn: 15 });
  });

  it('slows down to arrive on green', () => {
    const a = glosaAdvice(200, EXACT_RED, 13);
    expect(a.advice).toBe('slow');
    expect(a.speed).toBeCloseTo(200 / 21, 2);
    expect(a.greenIn).toBe(20);
  });

  it('goes when the green starts before arrival', () => {
    expect(glosaAdvice(300, EXACT_RED, 13).advice).toBe('go');
  });

  it('prepares to stop without a speed when the red is long', () => {
    const red = { ...EXACT_RED, min_s: 60, max_s: 60, next_green_min_s: 60, next_green_max_s: 60 };
    expect(glosaAdvice(100, red, 13)).toMatchObject({ advice: 'stop', speed: null });
  });

  it('gives no advice when timing is uncertain or preempted', () => {
    expect(glosaAdvice(200, { ...EXACT_RED, max_s: 45, next_green_max_s: 45 }, 13)).toMatchObject({ advice: 'none', greenIn: 20 });
    expect(glosaAdvice(200, { ...EXACT_RED, preempted: true }, 13).advice).toBe('none');
    expect(glosaAdvice(200, null, 13).advice).toBe('none');
  });
});
