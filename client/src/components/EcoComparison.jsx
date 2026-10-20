// Each car on the road against the SUMO fuel-curve model (sim/eco_selection.py), and which cars were
// chosen as eco cars from that comparison. Shown on the Driving page.
import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { fmt, isNum } from '../lib/format.js';

const STATUS = {
  eco: ['Eco car', 'good'],
  candidate: ['Next in line', ''],
  efficient: ['Close to the model', ''],
  too_little_data: ['Too little data', ''],
  no_radio: ['No radio (cannot get advice)', ''],
};
const KIND = { car: 'Car', eco_car: 'Eco car type', motorcycle: 'Motorcycle', bus: 'Bus', truck: 'Truck' };

// Format a signed percentage, or a dash when there is no value.
function pct(v) {
  if (!isNum(v)) return '–';
  return `${v > 0 ? '+' : ''}${fmt(v, 0)} %`;
}

const EVAL_ROWS = [
  ['co2_g_per_km', 'CO₂, all cars (g/km)', 1], ['fuel_l_per_100km', 'Fuel, all cars (L/100 km)', 2],
  ['stops_per_vehicle', 'Stops per vehicle', 2], ['avg_waiting_time', 'Average waiting time (s)', 1],
  ['eco_advised_vehicles', 'Eco cars that got advice', 0],
];
// Readable column name for a selection result, for example 'data@0.6' -> 'By data, 60 %'.
const EVAL_NAME = (n) => (n === 'none' ? 'No eco cars' : n.replace(/^(\w+)@(.*)$/, (_, m, f) => `${m === 'data' ? 'By data' : 'At random'}, ${Math.round(Number(f) * 100)} %`));

// Whole-network result on the same traffic: eco cars by data, at random, or none.
/** Same traffic, whole network: eco cars chosen by data vs at random vs none. */
function SelectionResults() {
  const [d, setD] = useState(undefined);
  useEffect(() => { api.ecoSelectComparison().then(setD).catch(() => setD(null)); }, []);
  if (!d) return null;
  const names = Object.keys(d.configs || {});
  return (
    <details>
      <summary className="small">Does choosing by data help? Same traffic, whole network ({d.meta?.controller} signals, seeds {(d.meta?.seeds || []).join(', ')})</summary>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Selection comparison (scrolls sideways on small screens)">
        <table className="data">
          <thead><tr><th>Metric</th>{names.map((n) => <th key={n}>{EVAL_NAME(n)}</th>)}</tr></thead>
          <tbody>
            {EVAL_ROWS.map(([k, label, dg]) => (
              <tr key={k}><td>{label}</td>
                {names.map((n) => {
                  const m = d.configs[n]?.[k];
                  return (
                    <td key={n} className="num">{m ? fmt(m.mean, dg) : '–'}
                      {isNum(m?.vs_none_pct) && k !== 'eco_advised_vehicles' && (
                        <span className={`small ${m.vs_none_pct < 0 ? 'good' : 'bad'}`}> ({m.vs_none_pct > 0 ? '+' : ''}{fmt(m.vs_none_pct, 1)} %)</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

// Driving page card: cars against the SUMO model, and why each eco car was chosen.
export default function EcoComparison({ eco, onFollow }) {
  const c = eco?.comparison;
  if (!c) return null;
  const data = eco.eco_select === 'data';
  const rows = c.rows || [];
  return (
    <section className="card stack" aria-labelledby="ecocmp-h">
      <div className="card-head">
        <h2 id="ecocmp-h">Cars against the SUMO fuel model</h2>
        <span className="chip">{data ? 'eco cars chosen from this data' : 'eco cars chosen at random'}</span>
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        For each car the simulation measures fuel, CO₂, distance, stops and time stopped every second. The model
        (SUMO&apos;s HBEFA4 fuel curves for the car&apos;s type, <code>sim/data/fuel_curves.json</code>) gives the fuel for
        the same distance driven smoothly at the car&apos;s own average speed. <strong>Above model</strong> is the extra fuel
        from stops, idling and accelerating, which eco-driving advice can save.
        {data && (
          <> Every 5 s the connected cars that burn the most above the model (at least {fmt(c.min_excess_pct, 0)} %,
          after {fmt(c.min_dist_m, 0)} m of driving) become eco cars, up to {fmt((eco.eco_fraction || 0) * 100, 0)} % of
          connected cars, and stay eco cars to the end of their trip.</>
        )}
      </p>
      <div className="tiles">
        <div className="tile"><div className="k">Cars compared now</div><div className="v num">{c.n_cars ?? 0}</div></div>
        <div className="tile"><div className="k">Eco cars on the road</div><div className="v num">{eco.eco_active ?? 0}</div>
          <div className="sub">{eco.eco_chosen_total ?? 0} chosen in this run</div></div>
        <div className="tile"><div className="k">Above model, eco cars</div><div className="v num">{pct(c.excess_pct_eco)}</div>
          <div className="sub">other cars {pct(c.excess_pct_other)}</div></div>
      </div>
      {data && (
        <p className="small muted" style={{ margin: 0 }}>
          Eco cars are picked because they waste fuel, so their numbers stay above the other cars&apos; and are not a
          fair test of the advice. The fair test runs the same traffic with eco cars chosen by data, at random and
          with none (<code>python sim/analytics/compare_eco_select.py</code>). Advice only helps on the way to a
          light with predictable timing (fixed-time signals), not in a queue.
        </p>
      )}
      <SelectionResults />
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Cars against the model (scrolls sideways on small screens)">
        <table className="data">
          <thead>
            <tr>
              <th>Car</th><th>Distance</th><th>Avg speed</th><th>Fuel (L/100 km)</th><th>CO₂ (g/km)</th>
              <th>Above model</th><th>Stops</th><th>Stopped</th><th>Decision</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const [label, cls] = STATUS[r.status] || [r.status, ''];
              return (
                <tr key={r.id} className={r.status === 'eco' ? 'selected' : ''}>
                  <td>
                    <button type="button" className="linkish" onClick={() => onFollow?.(r.id)} title="Follow this car">{r.id}</button>
                    <span className="small muted"> {KIND[r.category] || r.category}</span>
                  </td>
                  <td className="num">{fmt(r.dist_m, 0)} m</td>
                  <td className="num">{fmt(r.avg_kmh, 0)} <span className="small muted">km/h (best {fmt(r.best_kmh, 0)})</span></td>
                  <td className="num">{fmt(r.fuel_l_100km, 1)} <span className="small muted">model {fmt(r.model_fuel_l_100km, 1)}</span></td>
                  <td className="num">{fmt(r.co2_g_km, 0)} <span className="small muted">model {fmt(r.model_co2_g_km, 0)}</span></td>
                  <td className="num"><strong className={isNum(r.excess_pct) && r.excess_pct >= (c.min_excess_pct ?? 10) ? 'bad' : ''}>{pct(r.excess_pct)}</strong>
</td>
                  <td className="num">{r.stops}</td>
                  <td className="num">{r.idle_s} s</td>
                  <td>
                    <span className={`chip ${cls}`}>{label}</span>
                    {r.reason && <div className="small muted">{r.reason}</div>}
                  </td>
                </tr>
              );
            })}
            {!rows.length && <tr><td colSpan={9} className="muted">No cars with data yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
