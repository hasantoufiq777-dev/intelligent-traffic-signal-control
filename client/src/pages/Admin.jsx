import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import NowPlaying from '../components/NowPlaying.jsx';
import { useAuth } from '../hooks/useAuth.js';
import { useLive } from '../hooks/useLiveSocket.js';

const REASON = { finished: 'arrived', released: 'handed back', idle: 'idle', left: 'driver left', admin: 'admin',
  time_limit: 'time limit', run_ended: 'run ended', error: 'error' };

function DriverAdmin({ seat }) {
  const [info, setInfo] = useState(null);
  const [msg, setMsg] = useState(null);
  const load = useCallback(() => api.driver().then(setInfo).catch((e) => setMsg({ error: true, text: e.message })), []);
  useEffect(() => { load(); }, [load, seat?.busy, seat?.enabled]);

  async function act(fn, ok) {
    setMsg(null);
    try {
      await fn();
      setMsg({ text: ok });
      load();
    } catch (err) {
      setMsg({ error: true, text: err.message });
    }
  }
  const s = seat ?? info;
  return (
    <section className="card stack" aria-labelledby="drv-h">
      <div className="card-head"><h2 id="drv-h">Driver mode</h2><span className="small muted">one viewer drives a car at a time</span></div>
      <p style={{ margin: 0 }}>
        {s ? (s.enabled ? 'On' : 'Off') : '…'}
        {s?.busy ? ` · ${s.vid ?? 'a car'} is being driven since ${new Date(s.since).toLocaleTimeString()}` : ' · nobody is driving'}
      </p>
      <div className="row">
        <button type="button" className="btn" disabled={!s}
          onClick={() => act(() => api.setDriver(!s.enabled), s.enabled ? 'Driver mode switched off.' : 'Driver mode switched on.')}>
          {s?.enabled ? 'Switch driver mode off' : 'Switch driver mode on'}
        </button>
        <button type="button" className="btn danger" disabled={!s?.busy}
          onClick={() => act(() => api.releaseDriver(), 'The drive was ended and the car handed back.')}>End current drive</button>
      </div>
      {msg && <div className={`notice${msg.error ? ' error' : ''}`} role="status">{msg.text}</div>}
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
        <table className="data">
          <thead><tr><th>Started</th><th>Car</th><th>From → to</th><th>Ended</th><th>Time</th><th>CO₂ g/km</th><th>Stops</th></tr></thead>
          <tbody>
            {(info?.trips || []).map((t) => (
              <tr key={t.id}>
                <td>{new Date(t.started_at).toLocaleTimeString()}</td><td>{t.vid ?? '–'}</td><td>{t.from_edge} → {t.to_edge}</td>
                <td>{t.status === 'driving' ? 'driving' : REASON[t.reason] ?? t.status}</td>
                <td className="num">{t.report ? `${Math.round(t.report.travel_s)} s` : '–'}</td>
                <td className="num">{t.report?.co2_g_per_km ?? '–'}</td>
                <td className="num">{t.report ? t.report.stops : '–'}</td>
              </tr>
            ))}
            {info && !info.trips.length && <tr><td colSpan={7} className="muted">No drives yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function Admin() {
  const live = useLive();
  const { user, isAdmin } = useAuth();
  const [options, setOptions] = useState(null);
  const [form, setForm] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.options()
      .then((o) => { setOptions(o); setForm({ ...o.defaults }); })
      .catch((e) => setMsg({ error: true, text: e.message }));
  }, []);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function start(e) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const body = {
        ...form,
        seed: Number(form.seed),
        penetration: Number(form.penetration),
        loss: Number(form.loss),
        eco_fraction: Number(form.eco_fraction),
      };
      if (!body.network) delete body.network;
      const res = await api.start(body);
      setMsg({ text: `Started run ${res.run_id}` });
    } catch (err) {
      const issues = err.body?.issues?.map((i) => `${i.path}: ${i.message}`).join('; ');
      setMsg({ error: true, text: issues ? `${err.message}: ${issues}` : err.message });
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.stop();
      setMsg({ text: res.stopped ? 'Stopped. The playlist is paused until the next start.' : 'Nothing was running.' });
    } catch (err) {
      setMsg({ error: true, text: err.message });
    } finally {
      setBusy(false);
    }
  }

  async function playback(fn, ok) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      if (ok) setMsg({ text: ok });
    } catch (err) {
      setMsg({ error: true, text: err.message });
    } finally {
      setBusy(false);
    }
  }

  // What the gateway reports, so the buttons never offer an operation that would be refused
  const np = live.nowPlaying;
  const lifecycle = np?.lifecycle;
  const running = Boolean(np?.running);
  const transitional = lifecycle === 'STARTING' || lifecycle === 'STOPPING';
  const startedBy = running && np?.current?.source === 'admin' ? np.current.started_by : null;
  // Same rules as the gateway: a researcher may replace a playlist run or their own, and stop only their own
  const mine = np?.current?.started_by?.id === user.id;
  const canStart = isAdmin || !running || np?.current?.source !== 'admin' || mine;
  const canStop = isAdmin || (running && mine);
  // Pause and speed as the simulation confirmed them; same ownership rule as Stop
  const paused = Boolean(np?.playback?.paused);
  const speed = np?.playback?.speed ?? 1;
  const canPlayback = running && lifecycle === 'RUNNING' && canStop;
  const canRestart = Boolean(np?.current) && !transitional && (isAdmin || mine);

  const sel = (k, list) => (
    <label className="field">{k}
      <select value={form[k]} onChange={set(k)}>{list.map((v) => <option key={v} value={v}>{v}</option>)}</select>
    </label>
  );
  const num = (k, attrs) => (
    <label className="field">{k}<input type="number" value={form[k]} onChange={set(k)} {...attrs} /></label>
  );

  return (
    <div className="stack">
      <NowPlaying nowPlaying={live.nowPlaying} latest={live.latest} viewers={live.viewers} status={live.status} />
      <section className="card stack" aria-labelledby="admin-h">
        <div className="card-head">
          <h2 id="admin-h">Live run control</h2>
          <span className="small muted">administrators and researchers · one shared simulation for all viewers</span>
        </div>
        {startedBy && (
          <p className="small muted" style={{ margin: 0 }}>
            The current run was started by {startedBy.id === user.id ? 'you' : startedBy.name}.
            {!canStart && ' Only an administrator can stop or replace it.'}
          </p>
        )}
        {form && options ? (
          <form onSubmit={start} className="stack">
            <div className="form-grid">
              {sel('scenario', options.scenarios)}
              {sel('controller', options.controllers)}
              {sel('nav', options.nav)}
              {sel('relay', options.relay)}
              {sel('ev_routing', options.ev_routing || ['off', 'on'])}
              <label className="field">network
                <select value={form.network || ''} onChange={set('network')}>
                  <option value="">scenario default</option>
                  {(options.networks || []).map((v) => <option key={v} value={v}>{v}</option>)}
                </select>
              </label>
              {num('seed', { min: 0, step: 1 })}
              {num('penetration', { min: 0, max: 1, step: 0.1 })}
              {num('loss', { min: 0, max: 1, step: 0.05 })}
              {num('eco_fraction', { min: 0, max: 1, step: 0.1 })}
            </div>
            <div className="row">
              <button type="submit" className="btn primary" disabled={busy || transitional || !canStart}>Start live run</button>
              <button type="button" className="btn danger" disabled={busy || transitional || !running || !canStop} onClick={stop}>Stop</button>
              <span className="chip" role="status">simulation: {paused ? 'paused' : (lifecycle || 'unknown').toLowerCase()}</span>
              {options.mock && <span className="chip">gateway uses the mock simulation</span>}
            </div>
            <div className="row" role="group" aria-label="Playback">
              <button type="button" className="btn" aria-pressed={paused} disabled={busy || !canPlayback}
                onClick={() => playback(paused ? api.resume : api.pause)}>{paused ? 'Resume' : 'Pause'}</button>
              <label className="row small">Speed
                <select value={String(speed)} disabled={busy || !canPlayback}
                  onChange={(e) => playback(() => api.speed(Number(e.target.value)))}>
                  {[0.5, 1, 2, 4].map((v) => <option key={v} value={String(v)}>{v}×</option>)}
                </select>
              </label>
              <button type="button" className="btn" disabled={busy || !canRestart}
                onClick={() => playback(api.restart, 'Restarted from the beginning with the same settings.')}>Restart from the beginning</button>
              <span className="small muted">Pause and speed change only how fast the run is shown; its simulated results stay the same.</span>
            </div>
          </form>
        ) : (
          <p className="muted">Loading options…</p>
        )}
        {msg && <div className={`notice${msg.error ? ' error' : ''}`} role="status">{msg.text}</div>}
      </section>

      {isAdmin && <DriverAdmin seat={live.driverSeat} />}

      <section className="card" aria-labelledby="pl-h">
        <div className="card-head"><h2 id="pl-h">Playlist</h2><span className="small muted">read-only · edit server/playlist.json</span></div>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
          <table className="data">
            <thead><tr><th>#</th><th>Label</th><th>Scenario</th><th>Controller</th><th>Nav</th><th>Seed</th><th>Eco</th><th>Ambulance routing</th></tr></thead>
            <tbody>
              {(options?.playlist || []).map((p, i) => (
                <tr key={i}>
                  <td>{i + 1}</td><td>{p.label || ''}</td><td>{p.scenario}</td><td>{p.controller}</td>
                  <td>{p.nav}</td><td>{p.seed}</td><td>{p.eco_fraction ? `${Math.round(p.eco_fraction * 100)}%` : '–'}</td>
                  <td>{p.ev_routing === 'on' ? 'on' : '–'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
