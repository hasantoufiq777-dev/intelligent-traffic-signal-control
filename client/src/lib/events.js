// Event kinds a viewer should notice (counted on the bell in the top bar).
// Simulation started/finished are listed in the event centre but not counted, so a playlist does not
// ring the bell for every run; stops, failures and system warnings are counted.
export const IMPORTANT_EVENTS = new Set(['incident_start', 'incident_detected', 'incident_end', 'zone_activated', 'zone_deactivated',
  'preempt_start', 'ev_reroute', 'alert_lost', 'zone_ev_priority', 'sim_stopped', 'sim_failed', 'sim_halted', 'capacity_reached']);
export const SYSTEM_EVENTS = ['sim_started', 'sim_finished', 'sim_stopped', 'sim_failed', 'sim_halted', 'capacity_reached'];
