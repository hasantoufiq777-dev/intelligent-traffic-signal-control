// Live traffic, Overview tab: the eco cars of this run, compared with other cars, at a glance.
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useEcoCars } from '../hooks/useEcoCars.js';
import { compareGroups, RATING, sortCars, summarise } from '../lib/ecoScore.js';
import { fmt, isNum } from '../lib/format.js';

const ROWS = [
  ['eco_pct', 'Eco score', '%', 1], ['fuel_l_100km', 'Fuel', 'L/100 km', 1], ['co2_g_km', 'CO₂', 'g/km', 0],
  ['stops', 'Stops', 'per car', 1], ['idle_s', 'Stopped', 's per car', 0],
];
const SHOWN = 5;

// Eco car count, live advice, eco against other cars, and the eco cars on the road.
export default function EcoOverview({ eco, onFollow }) {
  const { data, failed } = useEcoCars();
  const cars = data?.cars ?? [];
  const s = useMemo(() => summarise(cars), [cars]);
  const groups = useMemo(() => compareGroups(cars), [cars]);
  const onRoad = useMemo(() => sortCars(cars.filter((c) => c.eco_car && !c.finished), 'eco_pct', -1).slice(0, SHOWN), [cars]);
  const active = eco?.eco_active ?? cars.filter((c) => c.eco_car && !c.finished).length;
  const hasEco = active > 0 || cars.some((c) => c.eco_car);

  return (
    <section className="card stack" aria-labelledby="eco-overview-h">
      <div className="card-head">
        <h2 id="eco-overview-h">Eco cars</h2>
        <span className={`chip${hasEco ? ' good' : ''}`}>{hasEco ? `${active} on the road` : 'none in this run'}</span>
      </div>

      {failed && !data && <div className="notice error" role="alert">The eco data could not be loaded.</div>}

      <div className="tiles">
        <div className="tile"><div className="k">Average eco score</div><div className="v num">{isNum(s.average) ? `${fmt(s.average, 1)} %` : '–'}</div><div className="sub">{s.scored} of {s.total} cars scored</div></div>
        <div className="tile"><div className="k">Eco cars so far</div><div className="v num">{eco?.eco_chosen_total ?? groups.eco.n}</div><div className="sub">{eco?.advised_now ?? 0} slowing for green</div></div>
      </div>

      {hasEco ? (
        <>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Eco cars against other cars">
            <table className="data">
              <thead><tr><th>Average</th><th>Eco cars ({groups.eco.n})</th><th>Other cars ({groups.other.n})</th></tr></thead>
              <tbody>
                {ROWS.map(([k, label, unit, digits]) => (
                  <tr key={k}>
                    <td>{label} <span className="small muted">{unit}</span></td>
                    <td className="num">{fmt(groups.eco[k], digits)}</td>
                    <td className="num">{fmt(groups.other[k], digits)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {onRoad.length > 0 && (
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Eco cars on the road">
              <table className="data">
                <thead><tr><th>Eco car on the road</th><th>Eco score</th><th>Fuel</th><th>Stops</th></tr></thead>
                <tbody>
                  {onRoad.map((c) => (
                    <tr key={c.id}>
                      <td><button type="button" className="linkish" title="Follow this car" onClick={() => onFollow?.(c.id)}>{c.id}</button></td>
                      <td className="num">{isNum(c.eco_pct) ? `${fmt(c.eco_pct, 0)} % ${RATING[c.rating]?.label ?? ''}` : 'under 100 m'}</td>
                      <td className="num">{fmt(c.fuel_l_100km, 1)}</td>
                      <td className="num">{c.stops}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : (
        <p className="small muted" style={{ margin: 0 }}>
          To add eco cars, start a run in Run control with <strong>eco_fraction 0.6</strong>. The city scenario has them by default.
        </p>
      )}
      <Link className="small" to="/eco">Open Eco driving for every car</Link>
    </section>
  );
}
