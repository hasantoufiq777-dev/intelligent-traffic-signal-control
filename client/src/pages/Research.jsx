// Research pages backed by stored data: genetic-algorithm tuning (results/ga), the history of
// runs on the shared simulation with a side-by-side comparison, and the event centre.
import { useEffect, useMemo, useState } from 'react';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api } from '../api.js';
import ChartTooltip from '../components/ChartTooltip.jsx';
import { SERIES } from '../components/LiveCharts.jsx';
import { useLive } from '../hooks/useLiveSocket.js';
import { IMPORTANT_EVENTS, SYSTEM_EVENTS } from '../lib/events.js';
import { fmt, fmtP, isNum } from '../lib/format.js';

const when = (iso) => (iso ? new Date(iso).toLocaleString() : '–');

function useFetch(load, deps = []) {
  const [state, setState] = useState({ data: null, error: null });
  useEffect(() => {
    let live = true;
    setState({ data: null, error: null });
    load().then((data) => live && setState({ data, error: null })).catch((error) => live && setState({ data: null, error }));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

// ---------------------------------------------------------------- genetic algorithm
const TARGET_TEXT = {
  signals: { title: 'Signal and VANET parameters', what: 'Minimises average waiting time + ambulance travel time + missed preemptions for the controller adaptive_ev_vanet.' },
  aco: { title: 'ACO navigation parameters', what: 'Minimises the navigating cars’ travel time + uneven road load + a penalty for long route searches.' },
};

function GaTarget({ target, data }) {
  const { history: h, evaluation: ev } = data;
  const gens = h?.generations || [];
  const keys = h ? Object.keys(h.best_chromosome || {}) : [];
  const s = h?.settings;
  return (
    <section className="stack" aria-label={TARGET_TEXT[target].title}>
      <div className="card stack">
        <div className="card-head"><h2>{TARGET_TEXT[target].title}</h2>{h && <span className="small muted">tuned {when(h.generated_at)}</span>}</div>
        <p className="small" style={{ margin: 0 }}>{TARGET_TEXT[target].what}</p>
        {h && (
          <div className="tiles">
            <div className="tile"><div className="k">Fitness, default parameters</div><div className="v num">{fmt(h.default_fitness, 2)}</div></div>
            <div className="tile"><div className="k">Fitness, best found</div><div className="v num">{fmt(h.best_fitness, 2)}</div>
              <div className="sub">{fmt((100 * (h.default_fitness - h.best_fitness)) / h.default_fitness, 1)} % lower (tuning seeds)</div></div>
            <div className="tile"><div className="k">Generations × population</div><div className="v num">{s?.generations} × {s?.population}</div></div>
            <div className="tile"><div className="k">Simulations run</div><div className="v num">{h.n_simulations}</div><div className="sub">repeated candidates come from the cache</div></div>
          </div>
        )}
        {s && (
          <p className="small muted" style={{ margin: 0 }}>
            Tuning seeds {s.seeds?.join(', ')} · scenario {s.scenarios?.join(', ')} · {Math.round((s.condition?.penetration ?? 0) * 100)} % connected,
            loss {s.condition?.loss}, range {s.condition?.range} m · tournament selection, BLX-α crossover, Gaussian mutation, elitism.
          </p>
        )}
      </div>

      {gens.length > 0 && (
        <div className="card" aria-label="Convergence">
          <div className="card-head"><h2>Convergence</h2><span className="small muted">fitness per generation (lower is better)</span></div>
          <ResponsiveContainer width="100%" height={230}>
            <LineChart data={gens} margin={{ top: 4, right: 8, bottom: 12, left: -6 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="generation" stroke="var(--axis)" tickLine={false}
                label={{ value: 'Generation', position: 'insideBottom', offset: -6, fill: 'var(--text-muted)', fontSize: 12 }} />
              <YAxis stroke="var(--axis)" tickLine={false} width={52} domain={['auto', 'auto']} />
              <Tooltip content={<ChartTooltip labelFormatter={(l) => `Generation ${l}`} valueFormatter={(v) => fmt(v, 2)} />} />
              <Legend iconType="plainline" verticalAlign="top" wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="best_fitness" name="Best so far" stroke={SERIES[0]} strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="generation_best_fitness" name="Best of the generation" stroke={SERIES[2]} strokeWidth={1.5} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="mean_fitness" name="Generation mean" stroke={SERIES[1]} strokeWidth={1.5} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {keys.length > 0 && (
        <div className="card stack">
          <div className="card-head"><h2>Parameters</h2></div>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
            <table className="data">
              <thead><tr><th>Parameter</th><th>Default</th><th>Tuned</th></tr></thead>
              <tbody>{keys.map((k) => <tr key={k}><td>{k}</td><td>{fmt(h.default_chromosome?.[k], 3)}</td><td>{fmt(h.best_chromosome[k], 3)}</td></tr>)}</tbody>
            </table>
          </div>
        </div>
      )}

      {ev?.conditions?.length > 0 && (
        <div className="card stack">
          <div className="card-head"><h2>Check on held-out seeds</h2><span className="small muted">seeds {ev.seeds?.join(', ')} (not used for tuning)</span></div>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
            <table className="data">
              <thead><tr><th>Condition</th><th>Default fitness</th><th>Tuned fitness</th><th>Improvement</th><th>Seeds tuned better</th><th>p-value</th></tr></thead>
              <tbody>
                {ev.conditions.map((c, i) => (
                  <tr key={i}>
                    <td>{Math.round(c.condition.penetration * 100)} % connected, loss {c.condition.loss}{c.is_tuning_condition ? ' (tuning condition)' : ''}</td>
                    <td>{fmt(c.default_fitness_mean, 2)} ± {fmt(c.default_fitness_std, 2)}</td>
                    <td>{fmt(c.tuned_fitness_mean, 2)} ± {fmt(c.tuned_fitness_std, 2)}</td>
                    <td>{fmt(c.improvement_pct, 1)} %</td><td>{c.seeds_tuned_better} / {ev.seeds.length}</td><td>{fmtP(c.p_value_wilcoxon)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted" style={{ margin: 0 }}>Paired Wilcoxon signed-rank test over the seeds. A p-value above 0.05 means the gain is not statistically significant.</p>
        </div>
      )}
    </section>
  );
}

export function GaPage() {
  const { data, error } = useFetch(() => api.ga());
  const [target, setTarget] = useState('signals');
  if (error) return <div className="notice error" role="alert">{error.message}</div>;
  if (!data) return <div className="notice" role="status">Loading…</div>;
  const available = ['signals', 'aco'].filter((t) => data[t]);
  if (!available.length) {
    return <div className="notice">The genetic algorithm has not been run yet. Run <code>npm run sim:tune</code>, then reload.</div>;
  }
  const shown = available.includes(target) ? target : available[0];
  return (
    <div className="stack">
      <p className="page-lead">A genetic algorithm tunes parameters offline: each candidate is scored by running SUMO, the best survive and are recombined. The result is then checked on seeds the tuning never saw.</p>
      <div className="tabs" role="tablist" aria-label="Tuning target">
        {available.map((t) => (
          <button key={t} type="button" role="tab" aria-selected={shown === t} className={shown === t ? 'active' : ''} onClick={() => setTarget(t)}>
            {TARGET_TEXT[t].title}
          </button>
        ))}
      </div>
      <GaTarget target={shown} data={data[shown]} />
    </div>
  );
}

// ---------------------------------------------------------------- run history and comparison
const PAGE = 20;
const COMPARE = [
  ['avg_waiting_time', 'Average waiting time', 's', 2], ['avg_travel_time', 'Average travel time', 's', 2], ['avg_time_loss', 'Average time loss', 's', 2],
  ['throughput_veh_per_hr', 'Throughput', 'veh/h', 0], ['max_queue', 'Longest queue', 'veh', 0], ['ev_travel_time', 'Ambulance travel time', 's', 1],
  ['missed_preemptions', 'Missed preemptions', '', 0], ['pdr', 'Packet delivery ratio', '', 3], ['queue_estimate_mae', 'Queue estimate error', 'veh', 2],
  ['nav_avg_travel_time', 'Trip time, navigating cars', 's', 1], ['co2_g_per_km', 'CO₂', 'g/km', 1], ['fuel_l_per_100km', 'Fuel', 'L/100 km', 2],
  ['stops_per_vehicle', 'Stops per vehicle', '', 2], ['collisions', 'Collisions', '', 0], ['zone_conflicts', 'Zone conflicts', 's', 0],
  ['dispatched_ambulances', 'Ambulances sent during the run', '', 0], ['dispatched_avg_travel_time', 'Their average travel time', 's', 1],
];

/** Ambulances sent from Run control during a run: route, timing and where they got green. */
function DispatchedTrips({ runs }) {
  const withTrips = runs.filter((d) => d?.result?.metrics?.dispatched_trips?.length);
  if (!withTrips.length) return null;
  return withTrips.map((d) => {
    const m = d.result.metrics;
    return (
      <div key={d.run.id} className="stack">
        <h3 style={{ margin: 0 }}>Ambulances sent{runs.length > 1 ? ` (${runs.indexOf(d) === 0 ? 'A' : 'B'})` : ''}</h3>
        {m.dispatched_priority_added?.length > 0 && (
          <p className="small muted" style={{ margin: 0 }}>
            Ambulance priority was added to {m.dispatched_priority_added.join(', ')} when the first ambulance was sent
            (the run&apos;s controller has none of its own).
          </p>
        )}
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
          <table className="data">
            <thead><tr><th>Ambulance</th><th>From → to</th><th>Sent at</th><th>Travel time</th><th>Junctions</th><th>Green given at</th></tr></thead>
            <tbody>
              {m.dispatched_trips.map((t) => (
                <tr key={t.id}><td>{t.id}</td><td>{t.from} → {t.to}</td><td className="num">{t.dispatched_t} s</td>
                  <td className="num">{isNum(t.travel_time) ? `${t.travel_time} s` : 'not arrived'}</td>
                  <td>{t.junctions.join(', ') || '–'}</td>
                  <td>{t.priority_at.length}/{t.junctions.length}{t.priority_at.length ? ` (${t.priority_at.join(', ')})` : ''}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  });
}
const HIGHER_BETTER = new Set(['throughput_veh_per_hr', 'pdr']);
const describeRun = (r) => `${r.scenario} · ${r.controller}${r.nav && r.nav !== 'none' ? ` · nav ${r.nav}` : ''} · seed ${r.seed}`;

export function Runs() {
  const [offset, setOffset] = useState(0);
  const { data, error } = useFetch(() => api.runs(PAGE, offset), [offset]);
  const [picked, setPicked] = useState([]);
  const [details, setDetails] = useState({});
  const [loadError, setLoadError] = useState(null);

  useEffect(() => {
    for (const id of picked) {
      if (details[id]) continue;
      api.run(id).then((d) => setDetails((m) => ({ ...m, [id]: d }))).catch((e) => setLoadError(e.message));
    }
  }, [picked, details]);

  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]));
  const [a, b] = picked.map((id) => details[id]);
  const rows = COMPARE.filter(([k]) => picked.some((id) => isNum(details[id]?.result?.metrics?.[k])));

  return (
    <div className="stack">
      <p className="page-lead">Every run on the shared simulation is stored with its settings, result and events. Tick one run to see its result, or two to compare them.</p>
      {error && <div className="notice error" role="alert">{error.message}</div>}
      <section className="card stack" aria-labelledby="runs-h">
        <div className="card-head"><h2 id="runs-h">Runs</h2><span className="small muted">{data ? `${data.total} stored` : ''}</span></div>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
          <table className="data left">
            <thead><tr><th>Select</th><th>Started</th><th>Scenario</th><th>Controller</th><th>Navigation</th><th>Seed</th><th>Connected</th><th>Source</th><th>Status</th></tr></thead>
            <tbody>
              {(data?.runs || []).map((r) => (
                <tr key={r.id} className={picked.includes(r.id) ? 'selected' : ''}>
                  <td><input type="checkbox" checked={picked.includes(r.id)} disabled={!r.has_result} onChange={() => toggle(r.id)}
                    aria-label={`Select the run ${describeRun(r)} started ${when(r.started_at)}`} /></td>
                  <td>{when(r.started_at)}</td><td>{r.scenario}</td><td>{r.controller}</td><td>{r.nav}</td><td className="num">{r.seed}</td>
                  <td className="num">{isNum(r.penetration) ? `${Math.round(r.penetration * 100)} %` : '–'}</td>
                  <td>{r.source === 'admin' ? 'started by a user' : 'playlist'}</td>
                  <td>{r.status}{!r.has_result && r.status !== 'running' ? ' (no result)' : ''}</td>
                </tr>
              ))}
              {data && !data.runs.length && <tr><td colSpan={9} className="muted">No runs stored yet.</td></tr>}
              {!data && !error && <tr><td colSpan={9} className="muted">Loading…</td></tr>}
            </tbody>
          </table>
        </div>
        {data && data.total > PAGE && (
          <div className="row small" role="navigation" aria-label="Pages">
            <button type="button" className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</button>
            <span className="muted num">{offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}</span>
            <button type="button" className="btn" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Older</button>
          </div>
        )}
      </section>

      {loadError && <div className="notice error" role="alert">{loadError}</div>}
      {picked.length > 0 && (
        <section className="card stack" aria-labelledby="cmp-h">
          <div className="card-head">
            <h2 id="cmp-h">{picked.length === 2 ? 'Comparison' : 'Result'}</h2>
            <button type="button" className="btn" onClick={() => setPicked([])}>Clear selection</button>
          </div>
          {!a || (picked.length === 2 && !b) ? <p className="muted">Loading…</p> : (
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
              <table className="data">
                <thead>
                  <tr><th>Metric</th><th>A: {describeRun(a.run)}</th>{b && <th>B: {describeRun(b.run)}</th>}{b && <th>B against A</th>}</tr>
                </thead>
                <tbody>
                  {rows.map(([k, label, unit, digits]) => {
                    const va = a.result?.metrics?.[k];
                    const vb = b?.result?.metrics?.[k];
                    let diff = '–';
                    let cls = '';
                    if (b && isNum(va) && isNum(vb) && va !== 0) {
                      const pct = (100 * (vb - va)) / Math.abs(va);
                      diff = `${pct > 0 ? '+' : ''}${fmt(pct, 1)} %`;
                      if (Math.abs(pct) >= 0.05) cls = (pct > 0) === HIGHER_BETTER.has(k) ? 'good' : 'bad';
                    }
                    return (
                      <tr key={k}><td>{label}{unit && <span className="muted"> ({unit})</span>}</td><td>{fmt(va, digits)}</td>
                        {b && <td>{fmt(vb, digits)}</td>}{b && <td className={cls}>{diff}</td>}</tr>
                    );
                  })}
                  {!rows.length && <tr><td colSpan={4} className="muted">This run stored no result metrics.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
          {a && <DispatchedTrips runs={[a, b].filter(Boolean)} />}
          <p className="small muted" style={{ margin: 0 }}>
            Single runs differ by chance from seed to seed. The averaged, statistically tested comparisons are on the Results page.
            A run in which someone drove a car, or into which an ambulance was sent, is affected by it.
          </p>
        </section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- event centre
export const EVENT_GROUPS = {
  Incidents: ['incident_start', 'incident_detected', 'incident_end'],
  'Accident zone': ['zone_activated', 'zone_switch', 'zone_guided', 'zone_ev_priority', 'zone_deactivated'],
  Ambulance: ['ambulance_dispatched', 'preempt_start', 'preempt_end', 'ambulance_arrived', 'ambulance_rejected', 'ev_reroute',
    'alert_sent', 'alert_delivered', 'alert_lost'],
  Navigation: ['reroute'],
  Driving: ['driver_start', 'driver_end'],
  'Simulation and system': SYSTEM_EVENTS,
};
const SEVERITY = { incident_start: 'bad', alert_lost: 'bad', zone_activated: 'warn', incident_detected: 'warn', preempt_start: 'warn',
  zone_ev_priority: 'warn', ambulance_dispatched: 'warn', ambulance_rejected: 'bad', ambulance_arrived: 'good', incident_end: 'good', zone_deactivated: 'good', preempt_end: 'good',
  sim_failed: 'bad', sim_halted: 'bad', capacity_reached: 'warn', sim_stopped: 'warn', sim_finished: 'good' };

export function EventsPage() {
  const live = useLive();
  const [group, setGroup] = useState('all');
  const [importantOnly, setImportantOnly] = useState(false);
  const list = useMemo(() => live.events
    .filter((e) => (group === 'all' || EVENT_GROUPS[group].includes(e.kind)) && (!importantOnly || IMPORTANT_EVENTS.has(e.kind)))
    .slice().reverse(), [live.events, group, importantOnly]);
  const st = live.status;
  const np = live.nowPlaying;
  return (
    <div className="stack">
      <p className="page-lead">Events of the current run as the simulation reports them (the latest 200 are kept; every event is also stored with its run).</p>
      <section className="card stack" aria-labelledby="sysev-h">
        <div className="card-head"><h2 id="sysev-h">Simulation</h2><span className={`chip${np?.lifecycle === 'FAILED' ? ' bad' : ''}`}>{(np?.lifecycle || 'unknown').toLowerCase()}</span></div>
        <p style={{ margin: 0 }}>
          {np?.current ? `Run ${np.current.run_id}: ${np.current.entry.scenario} with ${np.current.entry.controller}, started ${when(np.current.started_at)}` : 'No run yet.'}
          {np?.current?.started_by ? ` by ${np.current.started_by.name}` : np?.current ? ' from the playlist' : ''}.
        </p>
        {st?.message && <p className="small muted" style={{ margin: 0 }}>Last status: {st.status} — {st.message}</p>}
        {np?.failures > 0 && <div className="notice error" role="alert">{np.failures} run(s) failed in a row{np.lifecycle === 'FAILED' ? '; the playlist is paused until a run is started from Run control.' : '.'}</div>}
        {live.connection !== 'connected' && <div className="notice error" role="alert">Not connected to the server: {live.connectionNote || live.connection}.</div>}
      </section>
      <section className="card stack" aria-labelledby="ev-h">
        <div className="card-head">
          <h2 id="ev-h">Events</h2>
          <div className="row">
            <label className="small">Category{' '}
              <select value={group} onChange={(e) => setGroup(e.target.value)}>
                <option value="all">all</option>
                {Object.keys(EVENT_GROUPS).map((g) => <option key={g} value={g}>{g}</option>)}
              </select>
            </label>
            <label className="small"><input type="checkbox" checked={importantOnly} onChange={(e) => setImportantOnly(e.target.checked)} /> important only</label>
          </div>
        </div>
        {list.length === 0 ? <p className="muted small" style={{ margin: 0 }}>No events{group !== 'all' || importantOnly ? ' match the filter' : ' in this run yet'}.</p> : (
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
            <table className="data left">
              <thead><tr><th>Time</th><th>Where</th><th>Event</th><th>Detail</th></tr></thead>
              <tbody>
                {list.map((e, i) => (
                  <tr key={`${e.t}-${i}-${e.kind}`}>
                    <td className="num">{e.t} s</td><td>{e.tls || '–'}</td>
                    <td className={SEVERITY[e.kind] || ''}>{e.kind.replaceAll('_', ' ')}</td><td className="wrap">{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
