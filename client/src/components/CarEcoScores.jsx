// How close each car of the run comes to its SUMO HBEFA4 fuel reference, as an eco percentage.
import { useMemo } from 'react';
import { useEcoCars } from '../hooks/useEcoCars.js';
import { usePersistentState } from '../hooks/usePersistentState.js';
import { carsCsv, RATING, sortCars, summarise } from '../lib/ecoScore.js';
import { fmt, isNum } from '../lib/format.js';

const PAGE = 20;
const KIND = { car: 'Car', eco_car: 'Eco car type', motorcycle: 'Motorcycle', bus: 'Bus', truck: 'Truck', emergency: 'Ambulance' };
const SORTS = [
  ['eco_pct', 'Eco %'], ['id', 'Car'], ['fuel_l_100km', 'Fuel'], ['co2_g_km', 'CO₂'], ['excess_pct', 'Above HBEFA4'],
  ['avg_kmh', 'Avg speed'], ['stops', 'Stops'], ['idle_s', 'Stopped'], ['dist_m', 'Distance'],
];
const FILTERS = [['all', 'All cars'], ['road', 'On the road'], ['finished', 'Finished'], ['connected', 'With radio'], ['eco', 'Eco cars']];
// Format a percentage, or a dash when there is no value.
const pct = (v) => (isNum(v) ? `${fmt(v, 1)} %` : '–');

// Save text as a CSV file in the browser.
function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

// Driving page table: each car's Eco % against HBEFA4, with filters, sorting, pages and CSV.
export default function CarEcoScores({ onFollow, onSelect, selectedId, source }) {
  const own = useEcoCars(!source);
  const { data, failed } = source ?? own;
  const [sort, setSort] = usePersistentState('eco-sort', { key: 'eco_pct', dir: -1 });
  const [filter, setFilter] = usePersistentState('eco-filter', 'all');
  const [page, setPage] = usePersistentState('eco-page', 0);

  const cars = data?.cars ?? [];
  const s = useMemo(() => summarise(cars), [cars]);
  const rows = useMemo(() => {
    const keep = { all: () => true, road: (c) => !c.finished, finished: (c) => c.finished, connected: (c) => c.connected, eco: (c) => c.eco_car }[filter];
    return sortCars(cars.filter(keep), sort.key, sort.dir);
  }, [cars, filter, sort]);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const current = Math.min(page, pages - 1);
  const shown = rows.slice(current * PAGE, (current + 1) * PAGE);

  const chooseSort = (key) => {
    setPage(0);
    setSort((o) => (o.key === key ? { key, dir: -o.dir } : { key, dir: key === 'id' ? 1 : -1 }));
  };

  return (
    <section className="card stack" aria-labelledby="carscore-h">
      <div className="card-head">
        <h2 id="carscore-h">Eco score of each car against SUMO HBEFA4</h2>
        {data?.controller && <span className="chip">{data.scenario} · {data.controller}{isNum(data.penetration) ? ` · ${fmt(data.penetration * 100, 0)} % connected` : ''}</span>}
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        <strong>Eco % = HBEFA4 reference fuel ÷ the car&apos;s measured fuel × 100</strong> (at most 100 %). The reference is the fuel
        SUMO&apos;s HBEFA4 curve for that vehicle type gives for the same distance, driven steadily at the car&apos;s own average
        speed. 100 % means it drove as frugally as that; 50 % means it burned twice as much, through stops, idling and
        accelerating. The score starts after 100 m.
      </p>
      {failed && !data && <div className="notice error" role="alert">The car data could not be loaded.</div>}
      <div className="tiles">
        <div className="tile"><div className="k">Average eco score</div><div className="v num">{pct(s.average)}</div><div className="sub">{s.scored} of {s.total} cars scored · {s.onRoad} on the road</div></div>
        <div className="tile"><div className="k">With radio / without</div><div className="v num">{pct(s.connected)}</div><div className="sub">without radio {pct(s.notConnected)}</div></div>
        <div className="tile"><div className="k">Eco cars / others</div><div className="v num">{s.nEcoCars ? pct(s.ecoCars) : '–'}</div><div className="sub">{s.nEcoCars ? `others ${pct(s.otherCars)}` : 'no eco cars in this run'}</div></div>
        <div className="tile"><div className="k">Ratings</div>
          <div className="small">{Object.entries(RATING).map(([k, r]) => <div key={k}><span className="num"><strong>{s.ratings[k]}</strong></span> {r.label} <span className="muted">({r.range})</span></div>)}</div>
        </div>
      </div>
      <div className="row" style={{ alignItems: 'center' }}>
        <div className="seg" role="group" aria-label="Show cars">
          {FILTERS.map(([k, label]) => (
            <button key={k} type="button" className={`seg-btn${filter === k ? ' active' : ''}`} aria-pressed={filter === k}
              onClick={() => { setFilter(k); setPage(0); }}>{label}</button>
          ))}
        </div>
        <button type="button" className="btn" disabled={!cars.length}
          onClick={() => download(`eco-scores-${data?.run_id ?? 'run'}.csv`, carsCsv(sortCars(cars, 'id', 1)))}>Download all cars (CSV)</button>
      </div>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Eco score per car (scrolls sideways on small screens)">
        <table className="data eco-score-table">
          <thead>
            <tr>
              {SORTS.map(([k, label]) => (
                <th key={k} aria-sort={sort.key === k ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none'}>
                  <button type="button" className="linkish" onClick={() => chooseSort(k)}>{label}{sort.key === k ? (sort.dir > 0 ? ' ↑' : ' ↓') : ''}</button>
                </th>
              ))}
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => {
              const r = RATING[c.rating];
              return (
                <tr key={c.id} className={c.id === selectedId ? 'selected' : ''}>
                  <td className="num">
                    <strong>{pct(c.eco_pct)}</strong>
                    {r ? <> <span className={`chip ${r.cls}`}>{r.label}</span></> : <span className="small muted"> under 100 m</span>}
                  </td>
                  <td title={c.emission_class}>
                    {c.finished && !onSelect ? c.id : (
                      <button type="button" className="linkish" title="Show this car's eco details" aria-pressed={c.id === selectedId}
                        onClick={() => (onSelect ? onSelect(c) : onFollow?.(c.id))}>{c.id}</button>
                    )}
                    <span className="small muted"> {KIND[c.kind] || c.kind}</span>
                  </td>
                  <td className="num">{fmt(c.fuel_l_100km, 1)} <span className="small muted">HBEFA4 {fmt(c.model_fuel_l_100km, 1)} L/100 km</span></td>
                  <td className="num">{fmt(c.co2_g_km, 0)} <span className="small muted">HBEFA4 {fmt(c.model_co2_g_km, 0)} g/km</span></td>
                  <td className="num">{isNum(c.excess_pct) ? `${c.excess_pct > 0 ? '+' : ''}${fmt(c.excess_pct, 0)} %` : '–'}</td>
                  <td className="num">{fmt(c.avg_kmh, 0)} <span className="small muted">km/h</span></td>
                  <td className="num">{c.stops}</td>
                  <td className="num">{c.idle_s} s</td>
                  <td className="num">{fmt(c.dist_m, 0)} m</td>
                  <td className="small">{c.finished ? 'Finished' : 'On the road'} · {c.connected ? 'radio' : 'no radio'}{c.eco_car ? ' · eco car' : ''}</td>
                </tr>
              );
            })}
            {!shown.length && <tr><td colSpan={10} className="muted">{data ? 'No cars to show yet.' : 'Loading…'}</td></tr>}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="row" style={{ alignItems: 'center' }}>
          <button type="button" className="btn" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous</button>
          <span className="small muted">Page {current + 1} of {pages} · {rows.length} cars</span>
          <button type="button" className="btn" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>Next</button>
        </div>
      )}
    </section>
  );
}
