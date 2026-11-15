// One-driver mode: take a vehicle, drive it (accelerate, brake, slow down, stop, or set a desired
// speed), see the signal advice, the estimated fuel-efficient speed with its reasons, ambulance
// warnings, and get a trip report at the end. One viewer drives at a time; the others can follow.
// Every input only sets the vehicle's target speed: SUMO still caps it at the speed limit, keeps safe
// gaps, brakes within limits and stops at red lights.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fmt, isNum, seconds } from '../lib/format.js';
import { roadChoices } from '../lib/roads.js';
import { CATEGORY_LABEL } from '../lib/vehicles.js';

const DRIVABLE = ['car', 'eco_car', 'motorcycle', 'bus', 'truck'];
const STEP_KMH = 5;
const ACTION_TEXT = { maintain: 'Keep this speed', increase: 'Speed up', reduce: 'Slow down' };

/** Fuel against steady speed for this vehicle (from SUMO's HBEFA4 model), with the current and
 * recommended speeds marked. */
function FuelCurve({ curve, current, target }) {
  if (!curve?.length) return null;
  const W = 260;
  const H = 110;
  const pad = { l: 34, r: 8, t: 8, b: 24 };
  const xs = curve.map((c) => c.kmh);
  const ys = curve.map((c) => c.l_100km);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = 0;
  const y1 = Math.max(...ys) * 1.1;
  const X = (v) => pad.l + ((v - x0) / (x1 - x0 || 1)) * (W - pad.l - pad.r);
  const Y = (v) => H - pad.b - ((v - y0) / (y1 - y0 || 1)) * (H - pad.t - pad.b);
  const at = (v) => {
    for (let i = 1; i < curve.length; i += 1) {
      if (v <= curve[i].kmh) {
        const f = (v - curve[i - 1].kmh) / (curve[i].kmh - curve[i - 1].kmh);
        return curve[i - 1].l_100km + f * (curve[i].l_100km - curve[i - 1].l_100km);
      }
    }
    return curve[curve.length - 1].l_100km;
  };
  const d = curve.map((c, i) => `${i ? 'L' : 'M'}${X(c.kmh).toFixed(1)} ${Y(c.l_100km).toFixed(1)}`).join(' ');
  const mark = (v, color, label) => isNum(v) && v >= x0 && v <= x1 && (
    <g>
      <circle cx={X(v)} cy={Y(at(v))} r={4} fill={color} stroke="var(--surface-1)" strokeWidth={1.5} />
      <title>{`${label}: ${fmt(v, 0)} km/h, ${fmt(at(v), 1)} L/100 km`}</title>
    </g>
  );
  return (
    <figure className="fuel-curve" style={{ margin: 0 }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
        aria-label={`Fuel at steady speeds: lowest ${fmt(Math.min(...ys), 1)} L/100 km. Current ${fmt(current, 0)} km/h, recommended ${fmt(target, 0)} km/h.`}>
        <line x1={pad.l} y1={H - pad.b} x2={W - pad.r} y2={H - pad.b} stroke="var(--border-strong)" />
        <line x1={pad.l} y1={pad.t} x2={pad.l} y2={H - pad.b} stroke="var(--border-strong)" />
        {[x0, Math.round((x0 + x1) / 2), x1].map((v) => (
          <text key={v} x={X(v)} y={H - 8} textAnchor="middle" className="axis-label">{v}</text>
        ))}
        {[0, y1 / 2, y1].map((v) => (
          <text key={v} x={pad.l - 4} y={Y(v) + 3} textAnchor="end" className="axis-label">{fmt(v, 0)}</text>
        ))}
        <path d={d} fill="none" stroke="var(--series-1)" strokeWidth={2} />
        {mark(current, 'var(--text-secondary)', 'Now')}
        {mark(target, 'var(--eco)', 'Recommended')}
      </svg>
      <figcaption className="small muted">Fuel (L/100 km) against steady speed (km/h) for this vehicle · grey: now · green: recommended</figcaption>
    </figure>
  );
}

function EcoSpeed({ eco, onUse }) {
  if (!eco?.ok) return null;
  return (
    <div className={`advice advice-${eco.action === 'maintain' ? 'go' : 'slow'}`}>
      <span className="small muted">Estimated fuel-efficient speed (next {eco.horizon_m} m)</span>
      <span className="headline">{fmt(eco.target_kmh, 0)} km/h · {ACTION_TEXT[eco.action]}</span>
      <ul className="small" style={{ margin: 0, paddingLeft: '1.1em' }}>
        {eco.reasons.map((r) => <li key={r.code}>{r.text}</li>)}
      </ul>
      {eco.current && eco.recommended && (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Fuel estimate for the distance ahead">
          <table className="data">
            <thead><tr><th /><th>Speed</th><th>Fuel</th><th>CO₂</th><th>Time</th><th>Stop</th></tr></thead>
            <tbody>
              {[['Now', eco.current], ['Recommended', eco.recommended]].map(([k, v]) => (
                <tr key={k}><td>{k}</td><td className="num">{fmt(v.speed_kmh, 0)} km/h</td><td className="num">{fmt(v.fuel_ml, 1)} mL</td>
                  <td className="num">{fmt(v.co2_g, 0)} g</td><td className="num">{fmt(v.time_s, 0)} s</td><td>{v.stop ? 'yes' : 'no'}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <FuelCurve curve={eco.curve} current={eco.current?.speed_kmh} target={eco.target_kmh} />
      <button type="button" className="btn" style={{ alignSelf: 'flex-start' }} onClick={() => onUse(eco.target_kmh)}>
        Drive at {fmt(eco.target_kmh, 0)} km/h
      </button>
      <span className="small muted">
        From SUMO&apos;s HBEFA4 fuel model for this vehicle type, the light&apos;s timing, the vehicle ahead and the traffic
        ahead. An estimate, not a proven optimum.
      </span>
    </div>
  );
}

const ENDED_TEXT = {
  released: 'You handed the car back. It finishes its trip as normal traffic.',
  idle: 'No input for a while, so the car was handed back to the simulation.',
  left: 'You left the page, so the car was handed back.',
  admin: 'The admin ended the drive.',
  time_limit: 'The time limit for one drive was reached.',
  run_ended: 'The live run ended.',
  finished: 'You reached your destination.',
  error: 'The drive could not start.',
};


function adviceText(a, hasLight) {
  if (!a) return { cls: 'none', head: 'Waiting for data…' };
  if (a.queue_ahead) return { cls: 'none', head: 'Queue ahead: follow the car in front' };
  if (a.advice === 'slow') return { cls: 'slow', head: `Ease off to ${fmt(a.speed_kmh, 0)} km/h`, sub: `You arrive as it turns green${isNum(a.green_in) ? ` (in ${seconds(a.green_in)})` : ''} instead of stopping.` };
  if (a.advice === 'go') return { cls: 'go', head: 'Keep going: you will make the green' };
  if (a.advice === 'stop') return { cls: 'stop', head: `Prepare to stop${isNum(a.green_in) ? `: green in ${seconds(a.green_in)}` : ''}`, sub: 'The red is too long to reach the light on green.' };
  if (!hasLight) return { cls: 'none', head: 'No traffic light ahead' };
  return { cls: 'none', head: 'No speed advice right now', sub: 'The light timing is not predictable (adaptive signals or an ambulance has priority), or it is still too far.' };
}

function TripReport({ report, ended, onAgain, onClose }) {
  const c = report?.compare;
  const rows = report ? [
    ['Travel time', seconds(report.travel_s), c ? seconds(c.travel_s) : '–'],
    ['Average speed', `${fmt(report.avg_speed_kmh, 0)} km/h`, c ? `${fmt(c.avg_speed_kmh, 0)} km/h` : '–'],
    ['Stops per km', fmt(report.stops_per_km, 2), c ? fmt(c.stops_per_km, 2) : '–'],
    ['Fuel (L/100 km)', fmt(report.fuel_l_per_100km, 1), c ? fmt(c.fuel_l_per_100km, 1) : '–'],
    ['CO₂ (g/km)', fmt(report.co2_g_per_km, 0), c ? fmt(c.co2_g_per_km, 0) : '–'],
  ] : [];
  const group = c ? (c.group === 'same_route' ? `${c.n} car${c.n === 1 ? '' : 's'} on the same route` : `all ${c.n} cars that finished`) : null;
  return (
    <div className="stack">
      <div className="notice" role="status">{ended?.message && ended.reason === 'error' ? ended.message : ENDED_TEXT[ended?.reason] ?? 'Your drive ended.'}</div>
      {report && (
        <>
          <h3>Trip report{report.finished ? '' : ' (so far)'}</h3>
          <div className="kv">
            <div><div className="k">Distance</div><div className="v num">{fmt(report.distance_m / 1000, 2)} km</div></div>
            <div><div className="k">Stops</div><div className="v num">{report.stops}</div></div>
            <div><div className="k">Fuel used</div><div className="v num">{fmt(report.fuel_ml / 1000, 2)} L</div></div>
            <div><div className="k">CO₂</div><div className="v num">{fmt(report.co2_g, 0)} g</div></div>
          </div>
          {rows.length > 0 && (
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
              <table className="data">
                <thead><tr><th /><th>You</th><th>Others{group ? <span className="small muted"> ({group})</span> : ''}</th></tr></thead>
                <tbody>{rows.map(([k, you, others]) => <tr key={k}><td>{k}</td><td className="num">{you}</td><td className="num">{others}</td></tr>)}</tbody>
              </table>
            </div>
          )}
          <p className="small muted" style={{ margin: 0 }}>
            {report.advice_seconds > 0
              ? `You got speed advice for ${report.advice_seconds} s and followed it ${report.advice_followed_pct}% of that time.`
              : 'You got no speed advice on this trip.'}
            {report.amb_warnings > 0 ? ` Ambulance warnings: ${report.amb_warnings}.` : ''}
            {' '}Others are compared per km because routes differ in length.
          </p>
        </>
      )}
      <div className="row">
        <button type="button" className="btn primary" onClick={onAgain}>Drive again</button>
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}

export default function DrivePanel({ live }) {
  const { network, driverSeat: seat, myDrive, startDrive, driveCommand, endDrive, clearDrive, follow, followId } = live;
  const choices = useMemo(() => roadChoices(network), [network]);
  // Vehicle categories this network has (the research networks: cars only)
  const kinds = useMemo(() => DRIVABLE.filter((k) => network?.vehicle_types?.some((t) => t.id === k))
    .filter((k) => k === 'car' || network?.edges?.some((e) => e.type && e.type !== 'grid')), [network]);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [ease, setEase] = useState(30);
  const [kind, setKind] = useState('car');
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const lastInput = useRef(Date.now());
  const [, tick] = useState(0);

  // Defaults: the first entry road and an exit road on another side of the network
  useEffect(() => {
    if (!choices.starts.length) return;
    if (!choices.starts.some((s) => s.id === from)) setFrom(choices.starts[0].id);
    if (!choices.ends.some((e) => e.id === to)) {
      const start = choices.byId[choices.starts[0].id];
      const other = choices.ends.find((e) => choices.byId[e.id].to !== start?.from);
      setTo((other ?? choices.ends[0])?.id ?? '');
    }
  }, [choices]); // only when the road map changes

  const driving = Boolean(myDrive?.active);
  const state = driving ? myDrive.last : null;

  // Ride along with your own car on the map
  useEffect(() => {
    if (driving && myDrive.vid && followId !== myDrive.vid) follow(myDrive.vid);
  }, [driving, myDrive?.vid, followId, follow]);

  // Idle countdown display
  useEffect(() => {
    if (!driving) return undefined;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [driving]);

  const send = useCallback(async (action, kmh) => {
    lastInput.current = Date.now();
    const res = await driveCommand(action, kmh);
    setMsg(res?.ok ? null : { error: true, text: res?.error || 'Command failed' });
  }, [driveCommand]);

  // Desired speed: hold at most this speed (never above the limit; SUMO also caps it)
  const limitKmh = state?.limit_kmh ?? 50;
  const setSpeed = useCallback((kmh) => {
    const v = Math.max(5, Math.min(Math.round(kmh), Math.round(limitKmh)));
    setEase(v);
    if (v >= Math.round(limitKmh)) send('go');
    else send('ease', v);
  }, [limitKmh, send]);
  const desired = state?.mode === 'ease' && isNum(state?.target_kmh) ? state.target_kmh
    : state?.mode === 'stop' ? 0 : (state?.speed_kmh ?? 0);
  const accelerate = useCallback(() => setSpeed(Math.max(desired, state?.speed_kmh ?? 0) + STEP_KMH), [desired, state, setSpeed]);
  const brake = useCallback(() => {
    const v = Math.min(desired, state?.speed_kmh ?? desired) - STEP_KMH;
    if (v < 5) send('stop');
    else setSpeed(v);
  }, [desired, state, send, setSpeed]);
  const slowDown = useCallback(() => setSpeed(Math.max(10, Math.round((state?.speed_kmh ?? 30) * 0.6))), [state, setSpeed]);

  // Keyboard: W or Up = accelerate, X or Down = brake, E = slow down, G = normal driving, S or Space = stop
  useEffect(() => {
    if (!driving) return undefined;
    const onKey = (ev) => {
      if (ev.repeat || ev.ctrlKey || ev.metaKey || ev.altKey) return;
      const tag = ev.target?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      const k = ev.key.toLowerCase();
      if (k === 'g') send('go');
      else if (k === 'w' || k === 'arrowup') { ev.preventDefault(); accelerate(); }
      else if (k === 'x' || k === 'arrowdown') { ev.preventDefault(); brake(); }
      else if (k === 'e') slowDown();
      else if (k === 's' || k === ' ') { ev.preventDefault(); send('stop'); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [driving, send, accelerate, brake, slowDown]);

  async function take() {
    setBusy(true);
    setMsg(null);
    const res = await startDrive(from, to, kinds.length > 1 ? kind : undefined);
    setBusy(false);
    if (!res?.ok) setMsg({ error: true, text: res?.error || 'Could not take a car' });
    else lastInput.current = Date.now();
  }

  async function handBack() {
    setBusy(true);
    await endDrive();
    setBusy(false);
  }

  const head = (
    <div className="card-head">
      <h2 id="drive-h">Drive a vehicle</h2>
      {seat?.busy && !driving && <span className="chip">someone is driving</span>}
      {driving && <span className="chip drive-chip">{myDrive.vid || 'your car'}</span>}
    </div>
  );

  // ---- your drive ended: report
  if (myDrive && !driving) {
    return (
      <section className="card stack" aria-labelledby="drive-h">
        {head}
        <TripReport report={myDrive.report} ended={myDrive.ended ?? { reason: myDrive.last?.status === 'finished' ? 'finished' : 'released' }}
          onAgain={() => { clearDrive(); setOpen(true); }} onClose={() => { clearDrive(); follow(null); }} />
      </section>
    );
  }

  // ---- driving
  if (driving) {
    const s = state;
    const waiting = !s || s.status === 'waiting';
    const adv = adviceText(s?.advice, Boolean(s?.tls));
    const idleLeft = Math.max(0, (myDrive.idleMs ?? 120000) - (Date.now() - lastInput.current));
    return (
      <section className="card stack" aria-labelledby="drive-h">
        {head}
        {s?.amb && (
          <div className="drive-alert" role="alert">
            <span aria-hidden="true">🚑</span> {s.amb.text}
          </div>
        )}
        {waiting ? (
          <p className="muted" style={{ margin: 0 }}>Your {CATEGORY_LABEL[s?.kind ?? kind]?.toLowerCase() ?? 'vehicle'} is entering the road at {myDrive.from}…{isNum(s?.waiting_s) && s.waiting_s > 3 ? ` (waiting for a gap, ${s.waiting_s} s)` : ''}</p>
        ) : (
          <div className="kv">
            <div>
              <div className="k">Speed</div>
              <div className="v num drive-speed">{fmt(s.speed_kmh, 0)} <span className="small muted">km/h</span></div>
              <div className="small muted">speed limit {fmt(s.limit_kmh, 0)} km/h</div>
            </div>
            <div>
              <div className="k">You asked for</div>
              <div className="v">{s.mode === 'stop' ? 'Stop' : s.mode === 'ease' ? `At most ${fmt(s.target_kmh, 0)} km/h` : 'Normal driving (up to the limit)'}</div>
              {s.holding && <div className="small">Stopping after the junction</div>}
              {s.kind && <div className="small muted">{CATEGORY_LABEL[s.kind]}</div>}
            </div>
          </div>
        )}

        <div className="drive-controls" role="group" aria-label="Driving controls">
          <button type="button" className="btn" onClick={accelerate} aria-keyshortcuts="W ArrowUp">Accelerate <kbd>W</kbd></button>
          <button type="button" className="btn" onClick={brake} aria-keyshortcuts="X ArrowDown">Brake <kbd>X</kbd></button>
          <button type="button" className={`btn${s?.mode === 'ease' ? ' primary' : ''}`} onClick={slowDown} aria-keyshortcuts="E">Slow down <kbd>E</kbd></button>
          <button type="button" className={`btn${s?.mode === 'stop' ? ' danger' : ''}`} onClick={() => send('stop')} aria-keyshortcuts="S">Stop <kbd>S</kbd></button>
          <button type="button" className={`btn${s?.mode === 'go' ? ' primary' : ''}`} onClick={() => send('go')} aria-keyshortcuts="G">Normal <kbd>G</kbd></button>
        </div>
        <label className="field">Desired speed: {ease} km/h (limit {fmt(limitKmh, 0)})
          <input type="range" min={5} max={Math.max(10, Math.round(limitKmh))} step={1} value={Math.min(ease, Math.round(limitKmh))}
            onChange={(e) => setEase(Number(e.target.value))}
            onMouseUp={() => setSpeed(ease)} onKeyUp={() => setSpeed(ease)} onTouchEnd={() => setSpeed(ease)} />
        </label>

        {!waiting && (
          <div className={`advice advice-${adv.cls}`}>
            <span className="small muted">Advice from the roadside unit{s.tls ? ` at ${s.tls}${isNum(s.dist) ? ` · ${fmt(s.dist, 0)} m` : ''}` : ''}</span>
            <span className="headline">{adv.head}</span>
            {adv.sub && <span className="small">{adv.sub}</span>}
            {s.advice?.advice === 'slow' && isNum(s.advice.speed_kmh) && (
              <button type="button" className="btn" style={{ alignSelf: 'flex-start' }}
                onClick={() => { const v = Math.max(10, Math.min(50, Math.round(s.advice.speed_kmh))); setEase(v); send('ease', v); }}>
                Use advised speed ({fmt(s.advice.speed_kmh, 0)} km/h)
              </button>
            )}
            {isNum(s.advice_followed_pct) && <span className="small muted">You followed the advice {s.advice_followed_pct}% of the time so far.</span>}
          </div>
        )}

        {!waiting && <EcoSpeed eco={s.eco_speed} onUse={setSpeed} />}

        {!waiting && (
          <div className="kv">
            <div><div className="k">To go</div><div className="v num">{fmt(s.dist_left_m / 1000, 2)} km</div></div>
            <div><div className="k">Time</div><div className="v num">{seconds(s.elapsed_s)}</div></div>
            <div><div className="k">Stops</div><div className="v num">{s.stops}</div></div>
            <div><div className="k">Fuel / CO₂</div><div className="v num">{fmt(s.fuel_ml / 1000, 2)} L · {fmt(s.co2_g, 0)} g</div></div>
          </div>
        )}

        {msg && <div className={`notice${msg.error ? ' error' : ''}`} role="status">{msg.text}</div>}
        <div className="row">
          <button type="button" className="btn" disabled={busy} onClick={handBack}>Hand the car back</button>
          <span className="small muted">Destination {myDrive.to}. The seat is freed after {seconds(idleLeft / 1000)} without input.</span>
        </div>
        <p className="small muted" style={{ margin: 0 }}>
          Your controls set the vehicle&apos;s target speed only, never above the speed limit. The simulation still keeps safe
          gaps, brakes within limits, uses only lanes this vehicle may use and stops at red lights.
        </p>
      </section>
    );
  }

  // ---- not driving
  let body;
  if (!seat || !network) {
    body = <p className="muted" style={{ margin: 0 }}>Driving is possible while a live run is on.</p>;
  } else if (!seat.enabled) {
    body = <p className="muted" style={{ margin: 0 }}>Driving is switched off by the admin.</p>;
  } else if (seat.busy) {
    body = (
      <>
        <p className="muted" style={{ margin: 0 }}>
          Someone else is driving{seat.vid ? ` car ${seat.vid}` : ''}. One viewer drives at a time; you can follow their car.
        </p>
        {seat.vid && <div className="row"><button type="button" className="btn" onClick={() => follow(seat.vid)}>Follow their car</button></div>}
      </>
    );
  } else if (!seat.available) {
    body = <p className="muted" style={{ margin: 0 }}>Driving is possible while a live run is on. Wait for the next run.</p>;
  } else if (!open) {
    body = (
      <>
        <p className="muted" style={{ margin: 0 }}>
          Take a vehicle into the live traffic and drive it yourself: accelerate, brake, slow down, stop or set a speed.
          You get signal advice, an estimated fuel-efficient speed with its reasons, ambulance warnings and a trip report.
        </p>
        <div className="row"><button type="button" className="btn primary" onClick={() => setOpen(true)}>Drive</button></div>
      </>
    );
  } else {
    body = (
      <>
        <div className="form-grid">
          {kinds.length > 1 && (
            <label className="field">Vehicle
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                {kinds.map((k) => <option key={k} value={k}>{CATEGORY_LABEL[k]}</option>)}
              </select>
            </label>
          )}
          <label className="field">Start road
            <select value={from} onChange={(e) => setFrom(e.target.value)}>
              {choices.starts.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </label>
          <label className="field">Destination road
            <select value={to} onChange={(e) => setTo(e.target.value)}>
              {choices.ends.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </label>
        </div>
        <div className="row">
          <button type="button" className="btn primary" disabled={busy || !from || !to} onClick={take}>Take the car</button>
          <button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button>
        </div>
        <p className="small muted" style={{ margin: 0 }}>
          Keys while driving: W / ↑ accelerate, X / ↓ brake, E slow down, S or Space stop, G normal driving. The seat is
          freed after {seconds((seat.idle_ms ?? 120000) / 1000)} without input.
        </p>
      </>
    );
  }
  return (
    <section className="card stack" aria-labelledby="drive-h">
      {head}
      {body}
      {msg && <div className={`notice${msg.error ? ' error' : ''}`} role="status">{msg.text}</div>}
    </section>
  );
}
