const ICON = {
  preempt_start: ['🚑', 'Preemption started'],
  preempt_end: ['✅', 'Preemption ended'],
  alert_sent: ['📡', 'Alert sent'],
  alert_delivered: ['📶', 'Alert delivered'],
  alert_lost: ['✖', 'Alert lost'],
  incident_start: ['⚠', 'Incident'],
  incident_detected: ['🔎', 'Incident detected'],
  incident_end: ['✔', 'Incident cleared'],
  reroute: ['↪', 'Reroute'],
  ev_reroute: ['🚑', 'Ambulance rerouted'],
  ambulance_dispatched: ['🚑', 'Ambulance sent'],
  ambulance_arrived: ['🏥', 'Ambulance arrived'],
  ambulance_rejected: ['✖', 'Ambulance not sent'],
  driver_start: ['🕹', 'Viewer drives'],
  driver_end: ['🏁', 'Drive ended'],
  zone_activated: ['🚧', 'Accident zone'],
  zone_switch: ['⇆', 'Zone turn'],
  zone_guided: ['↰', 'Cars guided past the crash'],
  zone_ev_priority: ['🚑', 'Ambulance priority at the zone'],
  zone_deactivated: ['✔', 'Zone cleared'],
  // Platform events (from the gateway)
  sim_started: ['▶', 'Simulation started'],
  sim_finished: ['🏁', 'Simulation finished'],
  sim_stopped: ['⏹', 'Simulation stopped'],
  sim_failed: ['⛔', 'Simulation failed'],
  sim_halted: ['⛔', 'Playlist halted after repeated failures'],
  capacity_reached: ['⚠', 'Connection limit reached'],
};

export default function EventLog({ events, limit = 80, filter }) {
  const list = (filter ? events.filter((e) => filter(e.kind)) : events).slice(-limit).reverse();
  return (
    <section className="card" aria-labelledby="events-h">
      <div className="card-head"><h2 id="events-h">Events</h2><span className="small muted">{events.length} in history</span></div>
      {list.length === 0 ? (
        <p className="muted small" style={{ margin: 0 }}>No events yet.</p>
      ) : (
        <ul className="events" tabIndex={0} aria-label="Event list (scrolls)">
          {list.map((e, i) => {
            const [icon, label] = ICON[e.kind] || ['•', e.kind];
            return (
              <li key={`${e.t}-${i}-${e.kind}`}>
                <span className="t">{e.t}s</span>
                <span aria-hidden="true">{icon}</span>
                <span><strong>{label}</strong>{e.tls ? ` · ${e.tls}` : ''}<br /><span className="small muted">{e.detail}</span></span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
