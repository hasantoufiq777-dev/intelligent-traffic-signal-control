import { describe, expect, it } from 'vitest';
import { roadChoices } from './roads.js';

const network = {
  junctions: [{ id: 'J0', tls: true }, { id: 'N' }, { id: 'S' }],
  edges: [
    { id: 'N2J0', from: 'N', to: 'J0' }, { id: 'J02S', from: 'J0', to: 'S' },
    { id: 'S2J0', from: 'S', to: 'J0' }, { id: 'J02N', from: 'J0', to: 'N' },
  ],
};

describe('roadChoices (driver and ambulance start/destination lists)', () => {
  it('lists entry roads into a signalised junction and exit roads out of one', () => {
    const c = roadChoices(network);
    expect(c.starts.map((s) => s.id)).toEqual(['N2J0', 'S2J0']);
    expect(c.ends.map((s) => s.id)).toEqual(['J02S', 'J02N']);
    expect(c.starts[0].label).toBe('N → J0');
    expect(c.byId.J02S.to).toBe('S');
  });

  it('is empty before the road map has arrived', () => {
    expect(roadChoices(null)).toEqual({ starts: [], ends: [], byId: {} });
  });
});
