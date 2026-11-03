import { fmt, isNum } from '../lib/format.js';

function Compare({ label, eco, non, unit, digits = 1 }) {
  const diff = isNum(eco) && isNum(non) && non !== 0 ? ((eco - non) / non) * 100 : null;
  return (
    <div className="tile">
      <div className="k">{label}</div>
      <div className="v num">{fmt(eco, digits)} <span className="small muted">{unit}</span></div>
      <div className="sub num">
        others {fmt(non, digits)}
        {diff != null && <span className={diff < 0 ? 'good' : 'bad'}> ({diff > 0 ? '+' : ''}{diff.toFixed(0)}%)</span>}
      </div>
    </div>
  );
}

export default function EcoSummary({ eco }) {
  if (!eco) return null;
  const on = (eco.eco_fraction || 0) > 0;
  return (
    <section className="card stack" aria-labelledby="eco-h">
      <div className="card-head">
        <h2 id="eco-h">Eco-driving</h2>
        <span className="chip">{on ? `${Math.round(eco.eco_fraction * 100)}% of connected cars follow advice` : 'off in this run'}</span>
      </div>
      {!on && (
        <p className="muted small" style={{ margin: 0 }}>
          In runs with eco-driving, roadside units broadcast when each light turns green, and eco cars
          slow down early to arrive on green instead of stopping. Totals below cover finished trips.
        </p>
      )}
      <div className="tiles">
        {on && (
          <>
            <div className="tile"><div className="k">Eco cars on the road</div><div className="v num">{eco.eco_active ?? 0}</div></div>
            <div className="tile"><div className="k">Slowing for green now</div><div className="v num">{eco.advised_now ?? 0}</div></div>
          </>
        )}
        <Compare label="CO₂ per km (eco cars)" eco={eco.co2_g_per_km_eco} non={eco.co2_g_per_km_non} unit="g/km" digits={0} />
        <Compare label="Fuel (eco cars)" eco={eco.fuel_l_100km_eco} non={eco.fuel_l_100km_non} unit="L/100 km" />
        <Compare label="Stops per trip (eco cars)" eco={eco.stops_eco} non={eco.stops_non} unit="" />
        {on && (
          <div className="tile"><div className="k">Timing messages delivered</div><div className="v num">{isNum(eco.spat_pdr) ? `${Math.round(eco.spat_pdr * 100)}%` : '–'}</div></div>
        )}
      </div>
    </section>
  );
}
