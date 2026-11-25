// Run control form: restores saved values, keeping only those the gateway still accepts.

const CHOICES = {
  scenario: 'scenarios', controller: 'controllers', nav: 'nav', relay: 'relay', ev_routing: 'ev_routing', eco_select: 'eco_select',
};

export function restoreRunForm(saved, options) {
  const form = { ...options.defaults };
  for (const key of Object.keys(form)) {
    const value = saved?.[key];
    if (value === undefined || value === null || value === '') continue;
    const allowed = key === 'eco_routing' ? ['on', 'off'] : options[CHOICES[key]];
    if (key in CHOICES || key === 'eco_routing') {
      if (allowed?.includes(value)) form[key] = value;
    } else if (key !== 'network' && Number.isFinite(Number(value))) {
      form[key] = value;
    }
  }
  if (saved && 'network' in saved) form.network = saved.network ?? '';
  const networks = options.scenario_networks?.[form.scenario] ?? options.networks ?? [];
  if (form.network && !networks.includes(form.network)) form.network = '';
  return form;
}
