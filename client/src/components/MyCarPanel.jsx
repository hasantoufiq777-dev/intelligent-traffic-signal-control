import { fmt, kmh, seconds, SIGNAL_LABEL } from '../lib/format.js';
import { ADVICE_TEXT, glosaAdvice } from '../lib/glosa.js';
import { APPROACH_LABEL, FLAG, has, KIND_LABEL, vehicleKind } from '../lib/vehicles.js';

const DEFAULT_SPEED = 13.9; // m/s (50 km/h) when the car's own speed limit is unknown

function pickers(rows, onFollow) {
  const ids = (pred) => rows.filter((r) => pred(r[4])).map((r) => r[0]);
  const eco = ids((f) => has(f, FLAG.ECO));
  const amb = ids((f) => has(f, FLAG.EMERGENCY));
  const any = rows.map((r) => r[0]);
  const pick = (list) => list.length && onFollow(list[Math.floor(Math.random() * list.length)]);
  return (
    <div className="row">
      <button type="button" className="btn primary" disabled={!eco.length} onClick={() => pick(eco)}>Follow an eco car</button>
      <button type="button" className="btn" disabled={!any.length} onClick={() => pick(any)}>Follow any car</button>
      {amb.length > 0 && <button type="button" className="btn" onClick={() => pick(amb)}>Follow the ambulance</button>}
    </div>
  );
}

function countdownText(spat) {
  if (!spat) return null;
  if (spat.preempted) return { big: '—', small: 'Timing paused: emergency vehicle has priority' };
  const range = (a, b) => (a != null && b != null && Math.abs(b - a) > 1 ? `${seconds(a)} – ${seconds(b)}` : seconds(a));
  if (spat.signal === 'G') {
    return { big: seconds(spat.min_s), small: `Green for at least ${range(spat.min_s, spat.max_s)}` };
  }
  return { big: seconds(spat.next_green_min_s), small: `Green in ${range(spat.next_green_min_s, spat.next_green_max_s)}` };
}

function adviceHeadline(a, distM) {
  if (a.advice === 'slow') return `Ease off to ${kmh(a.speed)} km/h`;
  if (a.advice === 'go') return 'Keep going: you will make the green';
  if (a.advice === 'stop') return `Prepare to stop: red for another ${seconds(a.greenIn)}`;
  if (distM == null) return 'No traffic light ahead';
  return 'No advice: the light timing is not predictable right now';
}

export default function MyCarPanel({ followId, detail, rows, tlsState, onFollow, events = [] }) {
  if (!followId) {
    return (
      <section className="card stack" aria-labelledby="mycar-h">
        <div className="card-head"><h2 id="mycar-h">My car</h2></div>
        <p className="muted" style={{ margin: 0 }}>
          Tap a car on the map to ride along. You will see its next traffic light, a countdown, the
          speed advice it gets over the VANET, and the fuel it has used.
        </p>
        {pickers(rows, onFollow)}
      </section>
    );
  }

  if (!detail || detail.gone) {
    return (
      <section className="card stack" aria-labelledby="mycar-h">
        <div className="card-head"><h2 id="mycar-h">My car</h2><span className="chip">{followId}</span></div>
        <p className="muted" style={{ margin: 0 }}>
          {detail?.gone ? 'This car has reached its destination.' : 'Waiting for data about this car…'}
        </p>
        {pickers(rows, onFollow)}
        <button type="button" className="btn" onClick={() => onFollow(null)}>Stop following</button>
      </section>
    );
  }

  const kind = vehicleKind(detail.flags);
  const isEco = has(detail.flags, FLAG.ECO);
  const spat = detail.tls && detail.approach ? tlsState?.[detail.tls]?.spat?.[detail.approach] : null;
  const signal = detail.tls && detail.approach ? tlsState?.[detail.tls]?.signal?.[detail.approach] : null;
  const advice = isEco
    ? { advice: detail.advice || 'none', speed: detail.adv_speed, greenIn: detail.green_in }
    : glosaAdvice(detail.dist ?? 0, spat, DEFAULT_SPEED);
  const cd = countdownText(spat);

  return (
    <section className="card stack" aria-labelledby="mycar-h" aria-live="polite">
      <div className="card-head">
        <h2 id="mycar-h">My car</h2>
        <span className="chip" title={KIND_LABEL[kind]}>{detail.id} · {KIND_LABEL[kind]}</span>
      </div>

      <div className="kv">
        <div><div className="k">Speed</div><div className="v num">{kmh(detail.speed) ?? '–'} km/h</div></div>
        <div>
          <div className="k">Next traffic light</div>
          <div className="v">{detail.tls ? `${detail.tls}` : 'None ahead'}</div>
          {detail.approach && <div className="small muted">{APPROACH_LABEL[detail.approach]} · {fmt(detail.dist, 0)} m</div>}
        </div>
      </div>

      {detail.tls && (
        <div className="row" style={{ alignItems: 'center' }}>
          <span className={`sig sig-${signal || 'none'}`} aria-hidden="true">{signal ? signal.toUpperCase() : '?'}</span>
          <div>
            <div className="countdown num">{cd?.big ?? '–'}</div>
            <div className="small muted">{signal ? `${SIGNAL_LABEL[signal]} now. ` : ''}{cd?.small ?? 'No timing received'}</div>
          </div>
        </div>
      )}

      {kind === 'emergency' ? (
        <div className="advice advice-go">
          <span className="small muted">Emergency vehicle</span>
          <span className="headline">Priority: lights ahead turn green for this ambulance</span>
          <span className="small">Its alerts reach the roadside units over V2I/V2V; each junction on its route switches to green before it arrives (see Events).</span>
          {(() => {
            const last = [...events].reverse().find((e) => e.kind === 'ev_reroute' && e.detail?.startsWith(`${followId} `));
            return last
              ? <span className="small"><strong>Route:</strong> at {last.t}s {last.detail.slice(followId.length + 1)}</span>
              : null;
          })()}
        </div>
      ) : (
      <div className={`advice advice-${advice.advice}`}>
        <span className="small muted">
          {isEco ? 'Advice from the roadside unit (this car follows it)' : 'Suggested advice (this car is not an eco car and drives normally)'}
        </span>
        <span className="headline">{adviceHeadline(advice, detail.dist)}</span>
        {advice.advice === 'slow' && (
          <span className="small">{ADVICE_TEXT.slow} so you arrive as it turns green{advice.greenIn != null ? ` (in ${seconds(advice.greenIn)})` : ''}, instead of braking to a stop</span>
        )}
        {advice.advice === 'stop' && <span className="small">Coast towards the line; the red is too long to reach it on green</span>}
      </div>
      )}

      <div className="kv">
        <div><div className="k">Fuel used so far</div><div className="v num">{fmt((detail.fuel_ml ?? 0) / 1000, 2)} L</div></div>
        <div><div className="k">CO₂ so far</div><div className="v num">{fmt(detail.co2_g, 0)} g</div></div>
      </div>

      <div className="row">
        {pickers(rows, onFollow)}
        <button type="button" className="btn" onClick={() => onFollow(null)}>Stop following</button>
      </div>
    </section>
  );
}
