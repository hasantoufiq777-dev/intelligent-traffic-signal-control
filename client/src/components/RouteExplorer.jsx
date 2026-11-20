// Route explorer: the simulation's own A* (sim/navigation/route_explorer.py) between two roads, on the
// current traffic map. Shows every alternative with distance and times in consistent units, marks the
// fastest and the shortest, and draws them on the map (the selected one on top).
import { useMemo, useState } from 'react';
import { fmt, isNum } from '../lib/format.js';
import { ROUTE_COLORS } from './TrafficMap.jsx';

const VCLASSES = [['passenger', 'Car'], ['motorcycle', 'Motorcycle'], ['bus', 'Bus'], ['truck', 'Truck'],
  ['emergency', 'Ambulance']];

function roadLabel(e) {
  return `${e.from} → ${e.to}${e.type && e.type !== 'grid' ? ` (${e.type.replace('_', ' ')})` : ''}`;
}

export default function RouteExplorer({ live, result, onResult, selected, onSelect }) {
  const roads = useMemo(() => (live.network?.edges ?? []).slice().sort((a, b) => roadLabel(a).localeCompare(roadLabel(b))),
    [live.network]);
  const defaults = useMemo(() => {
    const ids = new Set(roads.map((r) => r.id));
    // city: a pair where the shorter route (local sub-road) is not the faster one (main road)
    if (ids.has('gEJ1') && ids.has('kcgS0')) return ['gEJ1', 'kcgS0'];
    return [roads[0]?.id ?? '', roads[roads.length - 1]?.id ?? ''];
  }, [roads]);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [vclass, setVclass] = useState('passenger');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const a = from || defaults[0];
  const b = to || defaults[1];
  const running = Boolean(live.nowPlaying?.running);

  async function search() {
    setBusy(true);
    setError(null);
    const res = await live.queryRoutes(a, b, vclass);
    setBusy(false);
    if (!res?.ok) {
      setError(res?.error || 'No answer');
      onResult(null);
      return;
    }
    onResult(res);
    onSelect(res.chosen ?? 0);
  }

  const routes = result?.routes ?? [];
  const fastest = routes.find((r) => r.fastest);
  const shortest = routes.find((r) => r.shortest);
  const st = result?.astar;

  return (
    <section className="card stack" aria-labelledby="routes-h">
      <div className="card-head"><h2 id="routes-h">Route explorer (A*)</h2></div>
      <p className="small muted" style={{ margin: 0 }}>
        The simulation searches with the same A* and road costs its navigating cars use, on the traffic as it is now.
      </p>
      <div className="form-grid">
        <label className="field">From
          <select value={a} onChange={(e) => setFrom(e.target.value)}>
            {roads.map((r) => <option key={r.id} value={r.id}>{roadLabel(r)}</option>)}
          </select>
        </label>
        <label className="field">To
          <select value={b} onChange={(e) => setTo(e.target.value)}>
            {roads.map((r) => <option key={r.id} value={r.id}>{roadLabel(r)}</option>)}
          </select>
        </label>
        <label className="field">Vehicle
          <select value={vclass} onChange={(e) => setVclass(e.target.value)}>
            {VCLASSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
      </div>
      <div className="row">
        <button type="button" className="btn primary" disabled={busy || !running || !a || !b || a === b} onClick={search}>
          {busy ? 'Searching…' : 'Find routes'}
        </button>
        {result && <button type="button" className="btn" onClick={() => onResult(null)}>Clear from the map</button>}
        {!running && <span className="small muted">Routes are searched while a live run is on.</span>}
      </div>
      {error && <div className="notice error" role="alert">{error}</div>}

      {result && (
        <>
          {result.mock && <div className="notice" role="status">The mock simulation has no road graph: this is placeholder data.</div>}
          {result.shorter_not_faster && fastest && shortest && (
            <div className="notice" role="status">
              The shortest route ({fmt(shortest.distance_m / 1000, 2)} km) is not the fastest now: the fastest is{' '}
              {fmt((fastest.distance_m - shortest.distance_m), 0)} m longer but{' '}
              {fmt((shortest.time_now_s ?? 0) - (fastest.time_now_s ?? 0), 0)} s quicker
              {shortest.signals > fastest.signals ? ' (fewer signals)' : ''}.
            </div>
          )}
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Route alternatives">
            <table className="data">
              <thead>
                <tr><th>Route</th><th>Distance</th><th>Free flow</th><th>Now</th><th>Signals</th><th>Slower than free</th></tr>
              </thead>
              <tbody>
                {routes.map((r, i) => (
                  <tr key={r.path.join(' ')} className={i === selected ? 'selected' : ''}>
                    <td>
                      <button type="button" className="linkish" aria-pressed={i === selected} onClick={() => onSelect(i)}>
                        <span className="swatch" style={{ background: ROUTE_COLORS[i % ROUTE_COLORS.length] }} aria-hidden="true" />
                        {i + 1}
                      </button>
                      {r.fastest && <span className="chip good">fastest</span>}
                      {r.shortest && <span className="chip">shortest</span>}
                    </td>
                    <td className="num">{fmt(r.distance_m / 1000, 2)} km</td>
                    <td className="num">{fmt(r.time_free_s, 0)} s</td>
                    <td className="num">{isNum(r.time_now_s) ? `${fmt(r.time_now_s, 0)} s` : 'closed'}</td>
                    <td className="num">{r.signals}</td>
                    <td className="num">{isNum(r.congestion) ? `${fmt((r.congestion - 1) * 100, 0)} %` : '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {routes[selected] && (
            <p className="small" style={{ margin: 0 }}>
              Route {selected + 1}: {routes[selected].path.join(' → ')}
            </p>
          )}
          {st && (
            <details>
              <summary className="small">How A* found route 1</summary>
              <ul className="small" style={{ margin: '6px 0 0', paddingLeft: '1.2em' }}>
                <li>f(n) = g(n) + h(n), all in seconds.</li>
                <li>g(n): travel time so far = Σ road length ÷ current speed (at least 1 m/s) + {fmt(st.signal_wait_s, 0)} s
                  for each signal the road ends at; a closed road costs ∞.</li>
                <li>h(n) = straight-line distance to the destination ÷ v_max. At the start: {fmt(st.straight_m, 0)} m ÷
                  {' '}{fmt(st.v_max, 2)} m/s = {fmt(st.h_start_s, 1)} s. No route can be shorter than the straight line or faster than
                  the highest speed limit, so h never overestimates and the route found is the cheapest.</li>
                <li>A* expanded {st.expansions} roads to reach the destination (cost {fmt(st.g_goal_s, 1)} s after the start road);
                  the shortest-distance search, with h = straight-line metres, expanded {st.expansions_distance}.</li>
                <li>Further alternatives: each road of route 1 closed in turn and A* run again.</li>
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
