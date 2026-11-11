// Event kinds a viewer should notice (counted on the bell in the top bar).
export const IMPORTANT_EVENTS = new Set(['incident_start', 'incident_detected', 'incident_end', 'zone_activated', 'zone_deactivated',
  'preempt_start', 'ev_reroute', 'alert_lost', 'zone_ev_priority']);
