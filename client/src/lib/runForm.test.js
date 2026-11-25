import { describe, expect, it } from 'vitest';
import { restoreRunForm } from './runForm.js';

const options = {
  defaults: { scenario: 'congested', controller: 'level_vanet', nav: 'none', seed: 1, penetration: 1, network: 'grid2x2', eco_routing: 'off' },
  scenarios: ['normal', 'congested', 'city'], controllers: ['level_vanet', 'fixed'], nav: ['none', 'aco'],
  networks: ['grid2x2', 'grid2x2_city'], scenario_networks: { normal: ['grid2x2'], congested: ['grid2x2'], city: ['grid2x2_city'] },
};

describe('run control form', () => {
  it('starts from the defaults', () => {
    expect(restoreRunForm(null, options)).toEqual(options.defaults);
  });

  it('keeps what the user chose', () => {
    expect(restoreRunForm({ scenario: 'normal', controller: 'fixed', penetration: '0.5', eco_routing: 'on' }, options))
      .toMatchObject({ scenario: 'normal', controller: 'fixed', penetration: '0.5', eco_routing: 'on' });
  });

  it('keeps "scenario default" as the network', () => {
    expect(restoreRunForm({ scenario: 'normal', network: '' }, options).network).toBe('');
  });

  it('drops values that are no longer valid', () => {
    const form = restoreRunForm({ controller: 'removed', seed: 'abc', unknown: 1 }, options);
    expect(form.controller).toBe('level_vanet');
    expect(form.seed).toBe(1);
    expect(form).not.toHaveProperty('unknown');
  });

  it('never restores a network the scenario cannot run on', () => {
    expect(restoreRunForm({ scenario: 'city', network: 'grid2x2' }, options).network).toBe('');
  });
});
