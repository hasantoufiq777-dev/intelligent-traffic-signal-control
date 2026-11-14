import { describe, expect, it } from 'vitest';
import { continuousAngle } from './vehicles.js';

describe('continuousAngle', () => {
  it('keeps the first heading as it is', () => {
    expect(continuousAngle(undefined, 270)).toBe(270);
  });
  it('turns the short way across 0/360', () => {
    expect(continuousAngle(10, 280)).toBe(-80); // left turn: -90 deg, not +270
    expect(continuousAngle(350, 80)).toBe(440); // right turn: +90 deg, not -270
  });
  it('follows a car round several turns without jumping', () => {
    let a;
    for (const h of [0, 90, 180, 270, 0, 90]) a = continuousAngle(a, h);
    expect(a).toBe(450);
  });
  it('makes small turns and no turn as expected', () => {
    expect(continuousAngle(90, 100)).toBe(100);
    expect(continuousAngle(-80, 280)).toBe(-80);
  });
});
