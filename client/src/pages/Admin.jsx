import { useEffect, useState } from 'react';
import { api } from '../api.js';
import NowPlaying from '../components/NowPlaying.jsx';
import { useLive } from '../hooks/useLiveSocket.js';

const TOKEN_KEY = 'itsc-admin-token';

function readToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}
function saveToken(v) {
  try {
    sessionStorage.setItem(TOKEN_KEY, v);
  } catch {
    /* storage unavailable: keep in memory only */
  }
}

export default function Admin() {
  const live = useLive();
  const [token, setToken] = useState(readToken);
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
      const res = await api.start(token, body);
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
      const res = await api.stop(token);
      setMsg({ text: res.stopped ? 'Stopped. The playlist is paused until the next start.' : 'Nothing was running.' });
    } catch (err) {
      setMsg({ error: true, text: err.message });
    } finally {
      setBusy(false);
    }
  }

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
        <div className="card-head"><h2 id="admin-h">Live run control</h2><span className="small muted">admin only</span></div>
        <label className="field" style={{ maxWidth: 360 }}>Admin token
          <input type="password" autoComplete="off" value={token}
            onChange={(e) => { setToken(e.target.value); saveToken(e.target.value); }} />
        </label>
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
              <button type="submit" className="btn primary" disabled={busy || !token}>Start live run</button>
              <button type="button" className="btn danger" disabled={busy || !token} onClick={stop}>Stop</button>
              {options.mock && <span className="chip">gateway uses the mock simulation</span>}
            </div>
          </form>
        ) : (
          <p className="muted">Loading options…</p>
        )}
        {msg && <div className={`notice${msg.error ? ' error' : ''}`} role="status">{msg.text}</div>}
      </section>

      <section className="card" aria-labelledby="pl-h">
        <div className="card-head"><h2 id="pl-h">Playlist</h2><span className="small muted">read-only · edit server/playlist.json</span></div>
        <div className="table-wrap">
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
