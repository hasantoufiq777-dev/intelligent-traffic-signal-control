// Unit tests for the number and time formatting shown across the dashboard.
import { describe, expect, it } from 'vitest';
import { fmt, fmtP, fmtPct, isNum, kmh, seconds, SIGNAL_LABEL } from './format.js';

describe('format', () => {
  it('isNum accepts finite numbers only', () => {
    expect([1, 0, -2.5].every(isNum)).toBe(true);
    expect([NaN, Infinity, '3', null, undefined].some(isNum)).toBe(false);
  });

  it('fmt rounds to the given digits and shows a dash for missing values', () => {
    expect(fmt(3.14159)).toBe('3.1');
    expect(fmt(3.14159, 2)).toBe('3.14');
    expect(fmt(null)).toBe('–');
    expect(fmt(NaN, 1, 'n/a')).toBe('n/a');
  });

  it('kmh converts m/s and keeps missing values missing', () => {
    expect(kmh(13.89)).toBe(50);
    expect(kmh(0)).toBe(0);
    expect(kmh(undefined)).toBeNull();
  });

  it('fmtPct shows the sign of a change', () => {
    expect(fmtPct(12.34)).toBe('+12.3%');
    expect(fmtPct(-5)).toBe('-5.0%');
    expect(fmtPct(0)).toBe('0.0%');
    expect(fmtPct(null)).toBe('–');
  });

  it('fmtP writes very small p-values as <0.001', () => {
    expect(fmtP(0.0004)).toBe('<0.001');
    expect(fmtP(0.0421)).toBe('0.042');
    expect(fmtP('x')).toBe('–');
  });

  it('seconds switches to minutes from 60 s and never goes negative', () => {
    expect(seconds(42.4)).toBe('42 s');
    expect(seconds(59.6)).toBe('1 min 0 s');
    expect(seconds(135)).toBe('2 min 15 s');
    expect(seconds(-3)).toBe('0 s');
    expect(seconds(null)).toBe('–');
  });

  it('names every signal colour', () => {
    expect(SIGNAL_LABEL).toEqual({ G: 'Green', y: 'Yellow', r: 'Red' });
  });
});
