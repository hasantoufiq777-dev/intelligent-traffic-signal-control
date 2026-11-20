import { describe, expect, it } from 'vitest';
import { accText, FOLLOW_TEXT, normalise, timeGap } from './following.js';

describe('car following', () => {
  it('describes acceleration for a driver', () => {
    expect(accText(-3)).toMatch(/braking hard/);
    expect(accText(-1)).toMatch(/slowing down/);
    expect(accText(1.2)).toMatch(/speeding up/);
    expect(accText(0.1)).toBe('steady speed');
    expect(accText(null)).toBe('unknown');
  });

  it('gives the time gap only while moving', () => {
    expect(timeGap(20, 10)).toBe(2);
    expect(timeGap(20, 0)).toBeNull();
  });

  it('has a text for every state the simulation sends', () => {
    for (const s of ['free', 'keep', 'slow', 'brake', 'stopped', 'speed_up', 'too_close']) {
      expect(FOLLOW_TEXT[s].head).toBeTruthy();
      expect(FOLLOW_TEXT[s].behind).toBeTruthy();
    }
  });

  it('normalises the followed car detail (m/s)', () => {
    const n = normalise({
      id: 'a', speed: 10, acc: -1, follow: 'slow', follow_speed: 8, ttc: 6,
      ahead: { id: 'b', speed: 7, acc: -2, gap: 20, kind: 'car', flags: 16 },
      behind: { id: 'c', speed: 10, acc: -0.5, gap: 15, kind: 'bus', flags: 0, follow: 'slow', follow_speed: 9 },
    });
    expect(n.ahead).toMatchObject({ id: 'b', gap: 20, v2v: true });
    expect(n.behind).toMatchObject({ id: 'c', v2v: false, follow: 'slow', followSpeed: 9 });
    expect(n.advice).toEqual({ state: 'slow', speed: 8, ttc: 6 });
  });

  it('treats no vehicle ahead as a free road', () => {
    expect(normalise({ id: 'a', speed: 10, ahead: null, behind: null }).advice).toEqual({ state: 'free' });
  });

  it('normalises the driver message (km/h)', () => {
    const n = normalise({
      speed_kmh: 36, acc: 0.2, ahead: { id: 'b', speed_kmh: 18, acc: -1, gap_m: 12, kind: 'car', v2v: true }, behind: null,
      follow: { state: 'slow', speed_kmh: 27, ttc: 2.4 },
    }, 'driver');
    expect(n.speed).toBeCloseTo(10);
    expect(n.ahead.speed).toBeCloseTo(5);
    expect(n.advice.speed).toBeCloseTo(7.5);
    expect(n.behind).toBeNull();
  });
});
