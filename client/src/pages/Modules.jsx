// Live module pages. Each one reads the shared live stream (useLive) and shows one aspect of the
// running simulation. A value the simulation did not send is shown as a dash, never invented.
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import EcoSummary from '../components/EcoSummary.jsx';
import JunctionPanel from '../components/JunctionPanel.jsx';
import { TimeChart } from '../components/LiveCharts.jsx';
import { AmbulanceList, IncidentList } from '../components/NetworkStatus.jsx';
import NowPlaying from '../components/NowPlaying.jsx';
import { NavPanel, VanetPanel } from '../components/VanetNavPanels.jsx';
import ZonePanel from '../components/ZonePanel.jsx';
import { useLive } from '../hooks/useLiveSocket.js';
import { fmt, isNum } from '../lib/format.js';

const ORDER = ['J2', 'J3', 'J0', 'J1'];
const DIRS = ['N', 'E', 'S', 'W'];
const FROM = { N: 'North', S: 'South', E: 'East', W: 'West' };
const junctionIds = (tls) => ORDER.filter((id) => tls[id]).concat(Object.keys(tls).filter((id) => !ORDER.includes(id)));
const noop = () => {};

function Shell({ lead, children }) {
  const live = useLive();
  return (
    <>
      <NowPlaying nowPlaying={live.nowPlaying} latest={live.latest} viewers={live.viewers} status={live.status} />
      {lead && <p className="page-lead">{lead}</p>}
      {!live.latest && <div className="notice" role="status" style={{ marginBottom: 16 }}>Waiting for the simulation to send data…</div>}
      <div className="stack">{children}</div>
    </>
  );
}

function Tile({ k, v, sub }) {
  return <div className="tile"><div className="k">{k}</div><div className="v num">{v}</div>{sub && <div className="sub">{sub}</div>}</div>;
}

function EventTable({ events, kinds, title, empty }) {
  const list = events.filter((e) => kinds.includes(e.kind)).slice(-40).reverse();
  return (
    <section className="card stack" aria-label={title}>
      <div className="card-head"><h2>{title}</h2><span className="small muted">{list.length ? `latest ${list.length}` : ''}</span></div>
      {list.length === 0 ? <p className="muted small" style={{ margin: 0 }}>{empty}</p> : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
          <table className="data left">
            <thead><tr><th>Time</th><th>Where</th><th>Event</th><th>Detail</th></tr></thead>
            <tbody>
              {list.map((e, i) => (
                <tr key={`${e.t}-${i}`}><td className="num">{e.t} s</td><td>{e.tls || '–'}</td><td>{e.kind.replaceAll('_', ' ')}</td><td className="wrap">{e.detail}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- signals
const PHASE_COLOR = { NS_GREEN: 'var(--series-1)', EW_GREEN: 'var(--series-3)', NS_YELLOW: 'var(--sig-yellow)', EW_YELLOW: 'var(--sig-yellow)',
  ALL_RED: 'var(--sig-red)', EV_GREEN: 'var(--ev-route)' };
const PHASE_LEGEND = [['NS_GREEN', 'North–south green'], ['EW_GREEN', 'East–west green'], ['NS_YELLOW', 'Yellow'], ['ALL_RED', 'All red'],
  ['EV_GREEN', 'Ambulance green'], [null, 'Other / transition']];

/** Phase of one junction over the stored history (last two simulated minutes), as a strip. */
function PhaseStrip({ id, states }) {
  const segs = useMemo(() => {
    const out = [];
    for (const s of states) {
      const phase = s.tls?.[id]?.phase;
      if (!phase) continue;
      const last = out[out.length - 1];
      if (last && last.phase === phase) last.n += 1;
      else out.push({ phase, n: 1, t: s.t });
    }
    return out;
  }, [id, states]);
  const total = segs.reduce((a, b) => a + b.n, 0) || 1;
  return (
    <div className="phase-row">
      <span className="phase-id">{id}</span>
      <div className="phase-strip" role="img" aria-label={`Phase history of ${id}: ${segs.slice(-6).map((s) => s.phase).join(', ')}`}>
        {segs.map((s, i) => (
          <span key={i} style={{ width: `${(s.n / total) * 100}%`, background: PHASE_COLOR[s.phase] || 'var(--text-muted)' }}
            title={`${s.phase} from t = ${s.t} s for about ${s.n} s`} />
        ))}
      </div>
    </div>
  );
}

const COMPARE_ROWS = [
  ['avg_waiting_time', 'Average waiting time', 's', 1],
  ['avg_queue', 'Average queue per junction', 'vehicles', 2],
  ['trips_completed', 'Vehicles through (completed trips)', '', 0],
  ['avg_travel_time', 'Average travel time', 's', 1],
  ['queue_veh_h', 'Total congestion (vehicle-hours queued)', 'h', 2],
  ['stops_per_vehicle', 'Stops per vehicle', '', 2],
  ['throughput_veh_per_hr', 'Throughput', 'veh/h', 0],
];

/** Fixed-time vs dynamic control on the same traffic (results/dynamic, sim/analytics/compare_dynamic.py). */
function SignalComparison() {
  const [data, setData] = useState(undefined);
  const [scen, setScen] = useState('normal');
  useEffect(() => { api.dynamicComparison().then(setData).catch(() => setData(null)); }, []);
  if (data === undefined) return null;
  const scenarios = Object.keys(data?.scenarios || {});
  const by = data?.scenarios?.[scenarios.includes(scen) ? scen : scenarios[0]] || {};
  const ctrls = ['fixed', 'adaptive', 'dynamic', 'dynamic_vanet'].filter((c) => by[c]);
  return (
    <section className="card stack" aria-labelledby="cmp-h">
      <div className="card-head">
        <h2 id="cmp-h">Fixed-time vs dynamic control</h2>
        {scenarios.length > 1 && (
          <div className="seg" role="group" aria-label="Scenario">
            {scenarios.map((s) => (
              <button key={s} type="button" className={`seg-btn${s === scen ? ' active' : ''}`} aria-pressed={s === scen}
                onClick={() => setScen(s)}>{s}</button>
            ))}
          </div>
        )}
      </div>
      {!data ? (
        <p className="muted small" style={{ margin: 0 }}>No comparison yet. Run <code>python sim/analytics/compare_dynamic.py</code>.</p>
      ) : (
        <>
          <p className="small muted" style={{ margin: 0 }}>
            Same traffic for every controller ({data.meta?.network}, seeds {(data.meta?.seeds || []).join(', ')},
            {' '}{data.meta?.max_steps} s each, VANET sensing with {fmt((data.meta?.penetration ?? 0) * 100, 0)} % of cars equipped).
            Mean over the seeds; the change is against fixed-time.
          </p>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Controller comparison (scrolls sideways on small screens)">
            <table className="data">
              <thead><tr><th>Metric</th>{ctrls.map((c) => <th key={c}>{data.labels?.[c] || c}</th>)}</tr></thead>
              <tbody>
                {COMPARE_ROWS.map(([k, label, unit, d]) => (
                  <tr key={k}>
                    <td>{label}{unit ? <span className="small muted"> ({unit})</span> : null}</td>
                    {ctrls.map((c) => {
                      const m = by[c]?.[k];
                      const pct = m?.vs_fixed_pct;
                      const lowerBetter = !['trips_completed', 'throughput_veh_per_hr'].includes(k);
                      const good = isNum(pct) && (lowerBetter ? pct < 0 : pct > 0);
                      return (
                        <td key={c} className="num">
                          {m ? fmt(m.mean, d) : '–'}
                          {isNum(pct) && pct !== 0 && <span className={`small ${good ? 'good' : 'bad'}`}> ({pct > 0 ? '+' : ''}{fmt(pct, 0)} %)</span>}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

export function Signals() {
  const live = useLive();
  const tls = live.latest?.tls || {};
  const ids = junctionIds(tls);
  const controller = live.nowPlaying?.current?.entry?.controller;
  const data = useMemo(() => live.states.slice(-120).map((s) => {
    const row = { t: s.t };
    for (const id of ids) row[id] = Object.values(s.tls?.[id]?.queues || {}).reduce((a, b) => a + b, 0);
    return row;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [live.states, ids.join()]);
  return (
    <Shell lead="By default the signals are dynamic: every second the controller measures each road (waiting cars, queue, density, waiting time, arrivals, room on the road ahead), gives green to the busier direction and sizes the green to its queue (5–45 s). Every change still goes green → yellow 3 s → all red → green; an approaching ambulance can take priority.">
      <div className={`module-grid${ids.some((id) => tls[id]?.dynamic) ? ' wide-cards' : ''}`}>
        {ids.map((id) => <JunctionPanel key={id} ids={[id]} selected={id} onSelect={noop} tlsState={tls} ev={live.latest?.ev} controller={controller} />)}
      </div>
      {ids.length > 0 && (
        <section className="card stack" aria-labelledby="ph-h">
          <div className="card-head"><h2 id="ph-h">Signal history</h2><span className="small muted">last two simulated minutes, oldest on the left</span></div>
          {ids.map((id) => <PhaseStrip key={id} id={id} states={live.states} />)}
          <div className="legend-row small">
            {PHASE_LEGEND.map(([k, label]) => (
              <span key={label}><i style={{ background: PHASE_COLOR[k] || 'var(--text-muted)' }} />{label}</span>
            ))}
          </div>
        </section>
      )}
      <TimeChart title="Vehicles waiting per junction" unit="vehicles" data={data} series={ids.map((id) => ({ key: id, name: id }))} />
      <SignalComparison />
      <EventTable events={live.events} kinds={['preempt_start', 'preempt_end']} title="Emergency preemption"
        empty="No preemption in this run so far." />
    </Shell>
  );
}

// ---------------------------------------------------------------- VANET
export function Vanet() {
  const live = useLive();
  const comm = live.latest?.comm;
  const vanet = live.network?.vanet;
  const tls = live.latest?.tls || {};
  const ids = junctionIds(tls);
  const counts = useMemo(() => {
    const c = {};
    for (const e of live.events) c[e.kind] = (c[e.kind] || 0) + 1;
    return c;
  }, [live.events]);
  const used = Boolean(comm?.sent);
  return (
    <Shell lead="Cars with a radio send a beacon every second; ambulances send alerts every 0.5 s. A message reaches a roadside unit (RSU) only within range, and may be lost or delayed on the emulated channel.">
      <div className="tiles">
        <Tile k="Radio range" v={vanet ? `${fmt(vanet.range_m, 0)} m` : '–'} />
        <Tile k="Cars with a radio" v={isNum(comm?.penetration) ? `${fmt(comm.penetration * 100, 0)} %` : vanet ? `${fmt(vanet.penetration * 100, 0)} %` : '–'} />
        <Tile k="Delivered" v={used && isNum(comm.pdr) ? `${fmt(comm.pdr * 100, 1)} %` : '–'} sub="packet delivery ratio" />
        <Tile k="Lost" v={used ? `${comm.lost} of ${comm.sent}` : '–'} sub={used ? `${fmt((100 * comm.lost) / comm.sent, 1)} % loss` : undefined} />
        <Tile k="Average delay" v={used ? `${fmt(comm.latency_ms, 1)} ms` : '–'} sub="channel: base 10 ms + up to 5 ms" />
        <Tile k="Jitter" v={used && isNum(comm.jitter_ms) ? `${fmt(comm.jitter_ms, 2)} ms` : '–'} sub="measured: spread (standard deviation) of the delay" />
        <Tile k="Relay hops" v={used && isNum(comm.avg_hops) ? fmt(comm.avg_hops, 2) : '–'} sub="ambulance alerts, V2V" />
        <Tile k="Alert lead time" v={used && isNum(comm.ev_alert_delivery_time) ? `${fmt(comm.ev_alert_delivery_time, 1)} s` : '–'}
          sub="alert reached the junction before the ambulance" />
        <Tile k="Missed preemptions" v={used ? comm.missed_preemptions ?? '–' : '–'} />
      </div>
      {!used && live.latest && <div className="notice">The controller of this run reads the simulation directly, so no VANET messages are sent. Runs with a “vanet” controller use the radio channel.</div>}
      <div className="module-grid">
        <VanetPanel comm={comm} states={live.states} />
        <div className="stack">
          <section className="card stack" aria-labelledby="rsu-h">
            <div className="card-head"><h2 id="rsu-h">Roadside units</h2><span className="small muted">queue from beacons against the true queue</span></div>
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
              <table className="data">
                <thead><tr><th>RSU</th><th>True queue</th><th>Estimate</th><th>Error</th><th>Phase</th></tr></thead>
                <tbody>
                  {ids.map((id) => {
                    const q = DIRS.reduce((a, d) => a + (tls[id].queues?.[d] ?? 0), 0);
                    const hasEst = tls[id].queues_est && used;
                    const est = hasEst ? DIRS.reduce((a, d) => a + (tls[id].queues_est?.[d] ?? 0), 0) : null;
                    return (
                      <tr key={id}><td>{id}</td><td>{q}</td><td>{est ?? '–'}</td><td>{est == null ? '–' : Math.abs(est - q)}</td><td>{tls[id].phase}</td></tr>
                    );
                  })}
                  {!ids.length && <tr><td colSpan={5} className="muted">No data yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
          <section className="card stack" aria-labelledby="msg-h">
            <div className="card-head"><h2 id="msg-h">Ambulance alert messages</h2><span className="small muted">events of this run (latest 200 kept)</span></div>
            <div className="tiles">
              <Tile k="Sent (EV_ALERT)" v={counts.alert_sent ?? 0} />
              <Tile k="Delivered to an RSU" v={counts.alert_delivered ?? 0} />
              <Tile k="Lost" v={counts.alert_lost ?? 0} />
            </div>
          </section>
        </div>
      </div>
      <EventTable events={live.events} kinds={['alert_sent', 'alert_delivered', 'alert_lost', 'incident_detected']} title="Message activity"
        empty="No alert or incident messages in this run so far." />
    </Shell>
  );
}

// ---------------------------------------------------------------- emergency vehicles
export function Emergency() {
  const live = useLive();
  const latest = live.latest;
  const tls = latest?.tls || {};
  const ids = junctionIds(tls);
  const routes = latest?.ev_routes || {};
  return (
    <Shell lead="An ambulance broadcasts its position and route. A junction it will reach within 18 s switches, through yellow and all red, to green for its approach only, then returns to normal control.">
      {latest && !latest.ev?.length && <div className="notice">No ambulance is on the road right now. The emergency scenarios send one in after about two simulated minutes.</div>}
      <AmbulanceList ev={latest?.ev} routes={routes} onFollow={live.follow} />
      {Object.entries(routes).map(([id, route]) => (
        <section className="card stack" key={id} aria-label={`Route of ${id}`}>
          <div className="card-head"><h2>Route of {id}</h2><span className="small muted">{route.length} roads left</span></div>
          <p className="route-line">{route.map((e, i) => <span key={`${e}-${i}`}>{i > 0 && <span aria-hidden="true"> → </span>}{e.startsWith(':') ? 'junction' : e}</span>)}</p>
        </section>
      ))}
      <section className="card stack" aria-labelledby="prio-h">
        <div className="card-head"><h2 id="prio-h">Junction priority</h2></div>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
          <table className="data left">
            <thead><tr><th>Junction</th><th>Phase</th><th>Ambulance</th><th>ETA</th><th>Distance</th><th>From</th></tr></thead>
            <tbody>
              {ids.map((id) => {
                const e = (latest?.ev || []).find((x) => x.tls === id);
                const pre = /^EV/.test(tls[id].phase || '');
                return (
                  <tr key={id}>
                    <td>{id}</td><td>{pre ? <strong className="bad">{tls[id].phase}</strong> : tls[id].phase}</td>
                    <td>{e?.id ?? '–'}</td><td className="num">{e ? `${fmt(e.eta)} s` : '–'}</td><td className="num">{e ? `${fmt(e.dist, 0)} m` : '–'}</td>
                    <td>{e ? FROM[e.approach] ?? '–' : '–'}</td>
                  </tr>
                );
              })}
              {!ids.length && <tr><td colSpan={6} className="muted">No data yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
      <EventTable events={live.events} kinds={['preempt_start', 'preempt_end', 'ev_reroute', 'zone_ev_priority']} title="Preemption and route changes"
        empty="No preemption or ambulance rerouting in this run so far." />
      <p className="small muted">The ambulance and its route are drawn on the <Link to="/">live map</Link>.</p>
    </Shell>
  );
}

// ---------------------------------------------------------------- navigation
/** The navigator's latest route search: the A* route, or the ACO alternatives with their probabilities. */
function RouteDecision({ d }) {
  return (
    <section className="card stack" aria-labelledby="dec-h">
      <div className="card-head"><h2 id="dec-h">Latest route decision</h2>
        {d && <span className="small muted">t = {d.t} s · car {d.vid}</span>}</div>
      {!d ? <p className="muted" style={{ margin: 0 }}>No route search yet in this run.</p> : (
        <>
          <p className="small" style={{ margin: 0 }}>
            From <strong>{d.from}</strong> to <strong>{d.to}</strong> with {d.mode === 'astar' ? 'A*' : 'ACO'}.
            {d.current_cost_s != null && <> Current route: {fmt(d.current_cost_s, 1)} s.</>}{' '}
            {d.applied ? 'The car switched to the chosen route.' : 'The car kept its route (the new one was not at least 5 % faster).'}
          </p>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Route alternatives">
            <table className="data">
              <thead><tr><th>Route</th><th>Estimated time</th><th>Probability</th><th>Chosen</th></tr></thead>
              <tbody>
                {d.routes.map((r, i) => (
                  <tr key={r.path.join('>')}>
                    <td className="small">{r.path.join(' → ')}</td>
                    <td className="num">{fmt(r.cost_s, 1)} s</td>
                    <td className="num">{fmt(r.prob * 100, 0)} %</td>
                    <td>{i === d.chosen ? '✓' : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

export function Navigation() {
  const live = useLive();
  const nav = live.latest?.nav;
  const off = !nav || nav.mode === 'none';
  return (
    <Shell lead="Navigating cars ask for the fastest route about every 30 s. A* returns the single best route; ant colony optimisation (ACO) finds up to three and spreads cars over them.">
      {live.latest && off && <div className="notice">Navigation is off in this run. The scenarios “navigation” and “incident” with A* or ACO show it.</div>}
      <div className="module-grid">
        <NavPanel nav={nav} states={live.states} />
        <div className="stack">
          <section className="card stack" aria-labelledby="cost-h">
            <div className="card-head"><h2 id="cost-h">How a route is chosen</h2></div>
            <ul className="plain-list">
              <li><strong>Road cost</strong> = length ÷ current speed from the traffic centre’s map + 8 s for each traffic light.</li>
              <li><strong>Closed road</strong>: infinite cost, so no route uses it.</li>
              <li><strong>ACO</strong>: an ant takes a turn with probability ∝ τ<sup>α</sup> · (1 ÷ cost)<sup>β</sup>; the best three routes are offered, each with a probability inversely proportional to its travel time.</li>
              <li><strong>Switching</strong>: only to a route at least 5 % faster, and at most once every 15 s.</li>
            </ul>
          </section>
          <RouteDecision d={nav?.last_decision} />
          <IncidentList incidents={live.latest?.incidents} zone={live.latest?.zone} />
        </div>
      </div>
      <EventTable events={live.events} kinds={['reroute', 'ev_reroute']} title="Route changes" empty="No route changes in this run so far." />
    </Shell>
  );
}

// ---------------------------------------------------------------- incidents
export function Incidents() {
  const live = useLive();
  const inc = live.latest?.incidents;
  const duration = useMemo(() => {
    const start = live.events.find((e) => e.kind === 'incident_start');
    const m = start?.detail?.match(/for (\d+)s/);
    return start && m ? { t: start.t, until: start.t + Number(m[1]) } : null;
  }, [live.events]);
  const t = live.latest?.t;
  return (
    <Shell lead="A crash is detected when a connected car has been stopped on a road for 30 s away from the stop line. The roadside unit reports it to the traffic centre, navigation avoids the road, and signals stop giving green to an approach that cannot move.">
      <div className="module-grid">
        <IncidentList incidents={inc} zone={live.latest?.zone} />
        <section className="card stack" aria-labelledby="clr-h">
          <div className="card-head"><h2 id="clr-h">Clearance</h2></div>
          {duration && isNum(t) && t < duration.until
            ? <div className="tiles"><Tile k="Started at" v={`${duration.t} s`} /><Tile k="Planned clearance" v={`${duration.until} s`} /><Tile k="Time left" v={`${Math.max(0, duration.until - t)} s`} sub="simulated seconds" /></div>
            : <p className="muted small" style={{ margin: 0 }}>{duration ? `The incident of this run ended at ${duration.until} s.` : 'No incident has started in this run.'}</p>}
          <p className="small muted" style={{ margin: 0 }}>The planned clearance time comes from the scenario; the flag at the traffic centre clears 10 s after the stopped vehicles are gone.</p>
        </section>
      </div>
      <ZonePanel zone={live.latest?.zone} />
      <EventTable events={live.events} kinds={['incident_start', 'incident_detected', 'incident_end', 'reroute', 'ev_reroute']} title="Incident timeline"
        empty="No incident in this run so far." />
      <p className="small muted">Blocked roads are marked on the <Link to="/">live map</Link>.</p>
    </Shell>
  );
}

// ---------------------------------------------------------------- accident work zone
const ZONE_RULES = [
  ['Turn order', 'green → yellow 3 s → both sides red until the shared lane is empty (at least 2 s) → the other side'],
  ['Turn length', '8 s + 2.5 s for each waiting vehicle, at most 40 s; ends early when its side is empty and the other waits'],
  ['Fairness', 'a side that has waited 90 s gets the next turn'],
  ['Safety', 'the next side gets green only when the lane and both ends are empty (counted at entry and exit, not by radio)'],
  ['Ambulance', 'its side gets the next turn and keeps it until the ambulance has entered'],
  ['Restoration', 'when the crash is cleared the zone signals switch off and both carriageways open'],
];

export function WorkZone() {
  const live = useLive();
  const zone = live.latest?.zone;
  const [zoneResults, setZoneResults] = useState(null);
  useEffect(() => {
    api.summary().then((s) => setZoneResults(s.suites?.zone?.accident_zone ?? {})).catch(() => setZoneResults({}));
  }, []);
  const cell = (c, m, digits = 1) => (c?.[m] ? `${fmt(c[m].mean, digits)}${isNum(c[m].improvement_pct) && c[m].improvement_pct !== 0 ? ` (${c[m].improvement_pct > 0 ? '−' : '+'}${fmt(Math.abs(c[m].improvement_pct), 1)} %)` : ''}` : '–');
  return (
    <Shell lead="When a crash blocks one side of the J2–J3 road, the open lane on the other side carries both directions, one at a time, with automatic signals at both ends.">
      {live.latest && !zone && <div className="notice">The current run has no accident zone. Start the scenario “accident_zone” (or wait for it in the playlist) to see alternating one-way traffic.</div>}
      <div className="module-grid">
        <div className="stack">
          <ZonePanel zone={zone} />
          {zone && (
            <div className="tiles">
              <Tile k="Safety" v={zone.conflicts === 0 ? 'OK' : `${zone.conflicts} s`} sub="seconds with both directions in the lane" />
              <Tile k="Direction turns" v={zone.switches} />
              <Tile k="Expected wait, eastbound" v={`${fmt(zone.expected_wait?.E, 0)} s`} />
              <Tile k="Expected wait, westbound" v={`${fmt(zone.expected_wait?.W, 0)} s`} />
            </div>
          )}
        </div>
        <section className="card stack" aria-labelledby="zr-h">
          <div className="card-head"><h2 id="zr-h">Rules</h2></div>
          <dl className="rules">{ZONE_RULES.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
        </section>
      </div>
      <section className="card stack" aria-labelledby="zres-h">
        <div className="card-head"><h2 id="zres-h">Evaluated results</h2><span className="small muted">10 seeds, mean; change against closing the road</span></div>
        {zoneResults && Object.keys(zoneResults).length > 0 ? (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
            <table className="data">
              <thead><tr><th>Configuration</th><th>Average trip (s)</th><th>Average waiting (s)</th><th>Ambulance trip (s)</th><th>Conflicts</th><th>Collisions</th></tr></thead>
              <tbody>
                {Object.entries(zoneResults).map(([label, c]) => (
                  <tr key={label}><td>{label.replaceAll('+', ' + ').replaceAll('_', ' ')}</td><td>{cell(c, 'avg_travel_time')}</td><td>{cell(c, 'avg_waiting_time')}</td>
                    <td>{cell(c, 'ev_travel_time')}</td><td>{fmt(c.zone_conflicts?.mean, 0)}</td><td>{fmt(c.collisions?.mean, 0)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="muted small" style={{ margin: 0 }}>{zoneResults ? 'The accident-zone evaluation has not been run yet.' : 'Loading…'}</p>}
        <p className="small muted" style={{ margin: 0 }}>Simulation results for this scenario, not a prediction for real roads. A minus sign means shorter than with the road closed.</p>
      </section>
      <EventTable events={live.events} kinds={['zone_activated', 'zone_switch', 'zone_guided', 'zone_ev_priority', 'zone_deactivated', 'incident_start', 'incident_detected', 'incident_end']}
        title="Zone timeline" empty="No accident-zone events in this run so far." />
    </Shell>
  );
}

// ---------------------------------------------------------------- GLOSA / eco-driving
function nextGreen(sp) {
  if (!sp || sp.next_green_min_s == null) return '–';
  const a = Math.ceil(sp.next_green_min_s);
  const b = sp.next_green_max_s == null ? null : Math.ceil(sp.next_green_max_s);
  return b == null || a === b ? `${a} s` : `${a}–${b} s`;
}

export function Glosa() {
  const live = useLive();
  const tls = live.latest?.tls || {};
  const ids = junctionIds(tls);
  const eco = live.latest?.eco;
  const detail = live.followed;
  return (
    <Shell lead="Each roadside unit broadcasts its signal timing (SPaT). An eco car works out when it will reach the stop line and gets advice: GO, SLOW (arrive as the light turns green), STOP, or none when the timing is not predictable.">
      <EcoSummary eco={eco} />
      {eco && ((eco.eco_fraction || 0) > 0 || (eco.eco_active ?? 0) > 0) && (
        <div className="tiles">
          <Tile k="SPaT messages delivered" v={isNum(eco.spat_pdr) ? `${fmt(eco.spat_pdr * 100, 0)} %` : '–'} />
          <Tile k="Stops per trip, eco cars" v={fmt(eco.stops_eco, 2)} sub={`others ${fmt(eco.stops_non, 2)}`} />
        </div>
      )}
      {detail && !detail.gone && (
        <section className="card stack" aria-label="Advice for the followed car">
          <div className="card-head"><h2>Followed car {detail.id}</h2><span className="chip">{detail.advice ? `advice: ${String(detail.advice).toUpperCase()}` : 'no advice (not an eco car, or none given)'}</span></div>
          <div className="tiles">
            <Tile k="Speed" v={isNum(detail.speed) ? `${fmt(detail.speed * 3.6, 0)} km/h` : '–'} />
            <Tile k="Advised speed" v={isNum(detail.adv_speed) ? `${fmt(detail.adv_speed * 3.6, 0)} km/h` : '–'} />
            <Tile k="Green in" v={isNum(detail.green_in) ? `${fmt(detail.green_in, 0)} s` : '–'} />
            <Tile k="Fuel so far" v={`${fmt((detail.fuel_ml ?? 0) / 1000, 2)} L`} sub={`CO₂ ${fmt(detail.co2_g, 0)} g (HBEFA4)`} />
          </div>
        </section>
      )}
      <section className="card stack" aria-labelledby="spat-h">
        <div className="card-head"><h2 id="spat-h">Signal phase and timing (SPaT)</h2><span className="small muted">as broadcast by each roadside unit</span></div>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
          <table className="data left">
            <thead><tr><th>Junction</th><th>From</th><th>Signal</th><th>Changes in</th><th>Next green in</th></tr></thead>
            <tbody>
              {ids.flatMap((id) => DIRS.map((d) => {
                const sp = tls[id].spat?.[d];
                const s = tls[id].signal?.[d];
                const change = sp?.min_s == null ? '–' : sp.max_s == null || Math.ceil(sp.min_s) === Math.ceil(sp.max_s) ? `${Math.ceil(sp.min_s)} s` : `${Math.ceil(sp.min_s)}–${Math.ceil(sp.max_s)} s`;
                return (
                  <tr key={`${id}-${d}`}><td>{d === 'N' ? id : ''}</td><td>{FROM[d]}</td>
                    <td><span className={`sig sig-${s || 'none'}`} aria-hidden="true">{s ? s.toUpperCase() : '?'}</span> {{ G: 'Green', y: 'Yellow', r: 'Red' }[s] || 'Unknown'}</td>
                    <td className="num">{change}</td><td className="num">{nextGreen(sp)}</td></tr>
                );
              }))}
              {!ids.length && <tr><td colSpan={5} className="muted">No data yet.</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="small muted" style={{ margin: 0 }}>A range means the adaptive controller may end the green anywhere in that window; a dash means it cannot be predicted (for example during ambulance priority). Follow a car on the <Link to="/">live map</Link> to see its own advice here.</p>
      </section>
    </Shell>
  );
}
