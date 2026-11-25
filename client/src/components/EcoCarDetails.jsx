// The eco details of one selected car: its score against HBEFA4, why it is an eco car, and its advice now.
import { useEcoCars } from '../hooks/useEcoCars.js';
import { RATING } from '../lib/ecoScore.js';
import { fmt, isNum, kmh } from '../lib/format.js';
import { FLAG, has } from '../lib/vehicles.js';

const KIND = { car: 'Car', eco_car: 'Eco car type', motorcycle: 'Motorcycle', bus: 'Bus', truck: 'Truck', emergency: 'Ambulance' };
const ADVICE = {
  go: 'GO: keep this speed, the light will be green',
  slow: 'SLOW: ease off to arrive on green',
  stop: 'STOP: the red is too long, stop at the line',
  none: 'No speed advice right now',
};

function Value({ k, v, sub }) {
  return <div className="tile"><div className="k">{k}</div><div className="v num">{v}</div>{sub && <div className="sub">{sub}</div>}</div>;
}

// id: the selected car. detail: its live data, when followed. source: shared eco data (else it loads its own).
// showRegular: on the Eco driving page, a selected regular car is shown too.
export default function EcoCarDetails({ id, detail, source, showRegular = false }) {
  const own = useEcoCars(!source);
  const { data } = source ?? own;
  const row = (data?.cars ?? []).find((c) => c.id === id);
  const live = detail && !detail.gone && detail.id === id ? detail : null;
  const isEco = Boolean(row?.eco_car || has(live?.flags ?? 0, FLAG.ECO));
  if (!id || (!isEco && !showRegular)) return null;
  const rating = RATING[row?.rating];
  const advice = live?.advice || 'none';

  return (
    <section className="card stack" aria-labelledby="eco-car-h">
      <div className="card-head">
        <h2 id="eco-car-h">{isEco ? 'Eco car' : 'Regular car'} {id}</h2>
        <span className={`chip ${rating?.cls ?? ''}`}>{isNum(row?.eco_pct) ? `${fmt(row.eco_pct, 1)} % · ${rating?.label ?? ''}` : 'scored after 100 m'}</span>
      </div>
      {!row && <p className="small muted" style={{ margin: 0 }}>Waiting for this car's fuel data…</p>}
      {isEco && row?.eco_reason && <p className="small" style={{ margin: 0 }}><strong>Why it is an eco car:</strong> {row.eco_reason}</p>}
      {live && isEco && (
        <div className={`advice advice-${advice}`}>
          <span className="small muted">Advice from the roadside unit</span>
          <span className="headline">{ADVICE[advice] ?? ADVICE.none}</span>
          <span className="small">
            Speed now <strong className="num">{kmh(live.speed) ?? '–'} km/h</strong>
            {isNum(live.adv_speed) && <> · advised <strong className="num">{kmh(live.adv_speed)} km/h</strong></>}
            {isNum(live.green_in) && <> · green in <strong className="num">{fmt(live.green_in, 0)} s</strong></>}
          </span>
        </div>
      )}
      {row && (
        <>
          <div className="tiles">
            <Value k="Fuel" v={`${fmt(row.fuel_l_100km, 1)}`} sub={`HBEFA4 ${fmt(row.model_fuel_l_100km, 1)} L/100 km`} />
            <Value k="CO₂" v={`${fmt(row.co2_g_km, 0)}`} sub={`HBEFA4 ${fmt(row.model_co2_g_km, 0)} g/km`} />
            <Value k="Above HBEFA4" v={isNum(row.excess_pct) ? `${row.excess_pct > 0 ? '+' : ''}${fmt(row.excess_pct, 0)} %` : '–'} sub="extra fuel" />
            <Value k="Average speed" v={`${fmt(row.avg_kmh, 0)} km/h`} sub={`most efficient ${fmt(row.best_kmh, 0)} km/h`} />
            <Value k="Stops" v={row.stops ?? '–'} sub={`stopped ${row.idle_s ?? 0} s`} />
            <Value k="Trip" v={`${fmt(row.dist_m, 0)} m`} sub={`${row.travel_s ?? 0} s · ${row.finished ? 'finished' : 'on the road'}`} />
          </div>
          <p className="small muted" style={{ margin: 0 }}>{KIND[row.kind] ?? row.kind} · {row.emission_class} · {row.connected ? 'VANET radio' : 'no radio'}</p>
        </>
      )}
    </section>
  );
}
