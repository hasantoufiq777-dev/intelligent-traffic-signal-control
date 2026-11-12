// One-driver mode: take a car, drive it with Go / Ease off / Stop, see the advice and ambulance
// warnings, and get a trip report at the end. One viewer drives at a time; the others can follow.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fmt, isNum, seconds } from '../lib/format.js';

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

/** Entry roads (start at a non-signal node) and exit roads (end at one), from the road map. */
function roadChoices(network) {
  if (!network) return { starts: [], ends: [] };
  const tls = new Set(network.junctions.filter((j) => j.tls).map((j) => j.id));
  const starts = network.edges.filter((e) => !tls.has(e.from) && tls.has(e.to))
    .map((e) => ({ id: e.id, label: `${e.from} → ${e.to}` }));
  const ends = network.edges.filter((e) => tls.has(e.from) && !tls.has(e.to))
    .map((e) => ({ id: e.id, label: `${e.from} → ${e.to}` }));
  return { starts, ends, byId: Object.fromEntries(network.edges.map((e) => [e.id, e])) };
}

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
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [ease, setEase] = useState(30);
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

  // Keyboard: G = go, E = ease off, S or Space = stop
  useEffect(() => {
    if (!driving) return undefined;
    const onKey = (ev) => {
      if (ev.repeat || ev.ctrlKey || ev.metaKey || ev.altKey) return;
      const tag = ev.target?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      const k = ev.key.toLowerCase();
      if (k === 'g') send('go');
      else if (k === 'e') send('ease', ease);
      else if (k === 's' || k === ' ') { ev.preventDefault(); send('stop'); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [driving, send, ease]);

  async function take() {
    setBusy(true);
    setMsg(null);
    const res = await startDrive(from, to);
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
      <h2 id="drive-h">Drive a car</h2>
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
          <p className="muted" style={{ margin: 0 }}>Your car is entering the road at {myDrive.from}…{isNum(s?.waiting_s) && s.waiting_s > 3 ? ` (waiting for a gap, ${s.waiting_s} s)` : ''}</p>
        ) : (
          <div className="kv">
            <div>
              <div className="k">Speed</div>
              <div className="v num drive-speed">{fmt(s.speed_kmh, 0)} <span className="small muted">km/h</span></div>
              <div className="small muted">limit {fmt(s.limit_kmh, 0)} km/h</div>
            </div>
            <div>
              <div className="k">You asked for</div>
              <div className="v">{s.mode === 'stop' ? 'Stop' : s.mode === 'ease' ? `At most ${fmt(s.target_kmh, 0)} km/h` : 'Go'}</div>
              {s.holding && <div className="small">Stopping after the junction</div>}
            </div>
          </div>
        )}

        <div className="drive-controls" role="group" aria-label="Driving controls">
          <button type="button" className={`btn${s?.mode === 'go' ? ' primary' : ''}`} onClick={() => send('go')} aria-keyshortcuts="G">Go <kbd>G</kbd></button>
          <button type="button" className={`btn${s?.mode === 'ease' ? ' primary' : ''}`} onClick={() => send('ease', ease)} aria-keyshortcuts="E">Ease off <kbd>E</kbd></button>
          <button type="button" className={`btn${s?.mode === 'stop' ? ' danger' : ''}`} onClick={() => send('stop')} aria-keyshortcuts="S">Stop <kbd>S</kbd></button>
        </div>
        <label className="field">Ease-off speed: {ease} km/h
          <input type="range" min={10} max={50} step={1} value={ease} onChange={(e) => setEase(Number(e.target.value))}
            onMouseUp={() => s?.mode === 'ease' && send('ease', ease)} onKeyUp={() => s?.mode === 'ease' && send('ease', ease)}
            onTouchEnd={() => s?.mode === 'ease' && send('ease', ease)} />
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
          Your buttons set the car&apos;s target speed only. The simulation still keeps safe gaps, brakes within limits and stops at red lights.
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
          Take a car into the live traffic and drive it yourself with Go, Ease off and Stop. You get the same
          speed advice and ambulance warnings as a connected car, and a trip report at the end.
        </p>
        <div className="row"><button type="button" className="btn primary" onClick={() => setOpen(true)}>Drive</button></div>
      </>
    );
  } else {
    body = (
      <>
        <div className="form-grid">
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
          Keys while driving: G go, E ease off, S or Space stop. The seat is freed after {seconds((seat.idle_ms ?? 120000) / 1000)} without input.
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
