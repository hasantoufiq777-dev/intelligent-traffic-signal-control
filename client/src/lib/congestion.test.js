import { describe, expect, it } from 'vitest';
import { congestionLevel } from '../components/NetworkStatus.jsx';
import { congestion } from '../components/TrafficMap.jsx';

describe('road congestion colouring', () => {
  it('needs a queue as well as a low speed on roads that end at a signal', () => {
    expect(congestion(1, 13.89, 1)).toBeNull(); // one car waiting at red is not congestion
    expect(congestion(1, 13.89, 4)).toBeNull(); // the old 'slow' level is gone
    expect(congestion(1, 13.89, 8)).toBe('congested');
    expect(congestion(8, 13.89, 8)).toBeNull(); // queue but traffic still moving (over 35 % of the limit)
    expect(congestion(12, 13.89, 10)).toBeNull(); // moving freely
  });
  it('marks an exit road only at a standstill, and nothing without data', () => {
    expect(congestion(5, 13.89, null)).toBeNull();
    expect(congestion(1, 13.89, null)).toBe('congested');
    expect(congestion(null, 13.89, 5)).toBeNull();
    expect(congestion(3, null, 5)).toBeNull();
  });
});

describe('network congestion level', () => {
  const state = (n, q) => ({ metrics: { n_vehicles: n }, tls: { J0: { queues: { N: q, S: 0, E: 0, W: 0 } } } });
  it('follows the share of vehicles standing in a queue', () => {
    expect(congestionLevel(state(40, 4)).text).toBe('Light');
    expect(congestionLevel(state(40, 12)).text).toBe('Moderate');
    expect(congestionLevel(state(40, 24)).text).toBe('Heavy');
  });
  it('gives nothing when the simulation sent no data', () => {
    expect(congestionLevel(null)).toBeNull();
    expect(congestionLevel({ metrics: { n_vehicles: 0 }, tls: {} })).toBeNull();
  });
});
