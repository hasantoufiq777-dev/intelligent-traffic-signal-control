// Administration (ADMIN only; the gateway enforces it): users and roles, active sessions,
// audit log, settings and system health.
import { useCallback, useEffect, useState } from 'react';
import { api } from '../api.js';
import { ROLE_LABEL, useAuth } from '../hooks/useAuth.js';

const PAGE = 25;
const ROLES = ['VIEWER', 'RESEARCHER', 'ADMIN'];
const when = (iso) => (iso ? new Date(iso).toLocaleString() : '–');

function useLoader(load, deps) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const reload = useCallback(() => {
    setError(null);
    return load().then(setData).catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, reload };
}

function Pager({ offset, total, onChange }) {
  if (total <= PAGE) return null;
  return (
    <div className="row small" role="navigation" aria-label="Pages">
      <button type="button" className="btn" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - PAGE))}>Previous</button>
      <span className="muted num">{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
      <button type="button" className="btn" disabled={offset + PAGE >= total} onClick={() => onChange(offset + PAGE)}>Next</button>
    </div>
  );
}

function Message({ msg }) {
  return msg ? <div className={`notice${msg.error ? ' error' : ''}`} role="status">{msg.text}</div> : null;
}

// ---------------------------------------------------------------- users
function Users() {
  const { user: me } = useAuth();
  const [offset, setOffset] = useState(0);
  const { data, error, reload } = useLoader(() => api.users(PAGE, offset), [offset]);
  const [form, setForm] = useState({ name: '', email: '', password: '', role: 'VIEWER' });
  const [msg, setMsg] = useState(null);
  const [link, setLink] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function act(fn, ok) {
    setMsg(null);
    setBusy(true);
    try {
      const res = await fn();
      if (ok) setMsg({ text: ok });
      await reload();
      return res;
    } catch (err) {
      const issues = err.body?.issues?.map((i) => i.message).join('; ');
      setMsg({ error: true, text: issues || err.message });
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function create(e) {
    e.preventDefault();
    const res = await act(() => api.createUser(form), `Account created for ${form.email}.`);
    if (res) setForm({ name: '', email: '', password: '', role: 'VIEWER' });
  }

  async function resetLink(u) {
    const res = await act(() => api.resetLink(u.id));
    if (res) setLink({ email: u.email, url: `${window.location.origin}${res.path}`, expires: res.expires_at });
  }

  return (
    <div className="stack">
      <section className="card stack" aria-labelledby="newu-h">
        <div className="card-head"><h2 id="newu-h">Add a user</h2></div>
        <form className="stack" onSubmit={create}>
          <div className="form-grid">
            <label className="field">Name<input value={form.name} onChange={set('name')} maxLength={80} required autoComplete="off" /></label>
            <label className="field">Email<input type="email" value={form.email} onChange={set('email')} required autoComplete="off" /></label>
            <label className="field">Initial password
              <input type="password" value={form.password} onChange={set('password')} minLength={10} required autoComplete="new-password" />
            </label>
            <label className="field">Role
              <select value={form.role} onChange={set('role')}>{ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}</select>
            </label>
          </div>
          <div className="row">
            <button type="submit" className="btn primary" disabled={busy || !form.name || !form.email || form.password.length < 10}>Add user</button>
            <span className="small muted">Passwords need at least 10 characters. Ask the user to change it after the first sign-in.</span>
          </div>
        </form>
        <Message msg={msg} />
        {link && (
          <div className="notice" role="status">
            Reset link for {link.email} (valid until {when(link.expires)}, single use). Send it to the user:
            <input className="copy-field" readOnly value={link.url} onFocus={(e) => e.target.select()} aria-label="Password reset link" />
          </div>
        )}
      </section>

      <section className="card stack" aria-labelledby="users-h">
        <div className="card-head"><h2 id="users-h">Users</h2><span className="small muted">{data ? `${data.total} accounts` : ''}</span></div>
        {error && <div className="notice error" role="alert">{error}</div>}
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
          <table className="data">
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last sign-in</th><th>Actions</th></tr></thead>
            <tbody>
              {(data?.users || []).map((u) => {
                const self = u.id === me.id;
                const locked = u.locked_until && new Date(u.locked_until) > new Date();
                return (
                  <tr key={u.id}>
                    <td>{u.name}{self && <span className="chip" style={{ marginLeft: 6 }}>you</span>}</td>
                    <td>{u.email}</td>
                    <td>
                      <select value={u.role} disabled={busy} aria-label={`Role of ${u.email}`}
                        onChange={(e) => act(() => api.updateUser(u.id, { role: e.target.value }), `${u.email} is now ${ROLE_LABEL[e.target.value]}.`)}>
                        {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
                      </select>
                    </td>
                    <td>{u.status === 'active' ? (locked ? 'locked (failed sign-ins)' : 'active') : 'disabled'}</td>
                    <td>{when(u.last_login_at)}</td>
                    <td>
                      <div className="row">
                        {u.status === 'active' && !locked
                          ? <button type="button" className="btn" disabled={busy || self} onClick={() => act(() => api.updateUser(u.id, { status: 'disabled' }), `${u.email} was disabled and signed out.`)}>Disable</button>
                          : <button type="button" className="btn" disabled={busy} onClick={() => act(() => api.updateUser(u.id, { status: 'active' }), `${u.email} can sign in again.`)}>{locked ? 'Unlock' : 'Enable'}</button>}
                        <button type="button" className="btn" disabled={busy} onClick={() => resetLink(u)}>Reset link</button>
                        <button type="button" className="btn danger" disabled={busy || self}
                          onClick={() => { if (window.confirm(`Delete the account ${u.email}? This cannot be undone.`)) act(() => api.deleteUser(u.id), `${u.email} was deleted.`); }}>
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {data && !data.users.length && <tr><td colSpan={6} className="muted">No users.</td></tr>}
              {!data && !error && <tr><td colSpan={6} className="muted">Loading…</td></tr>}
            </tbody>
          </table>
        </div>
        <Pager offset={offset} total={data?.total ?? 0} onChange={setOffset} />
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- sessions
function Sessions() {
  const [offset, setOffset] = useState(0);
  const { data, error, reload } = useLoader(() => api.sessions(PAGE, offset), [offset]);
  const [msg, setMsg] = useState(null);
  async function revoke(s) {
    setMsg(null);
    try {
      await api.revokeSession(s.id);
      setMsg({ text: `Signed out the session of ${s.email}.` });
      reload();
    } catch (err) {
      setMsg({ error: true, text: err.message });
    }
  }
  return (
    <section className="card stack" aria-labelledby="sess-h">
      <div className="card-head">
        <h2 id="sess-h">Active sessions</h2>
        <span className="row"><span className="small muted">{data ? `${data.total} signed in` : ''}</span>
          <button type="button" className="btn" onClick={reload}>Refresh</button></span>
      </div>
      {error && <div className="notice error" role="alert">{error}</div>}
      <Message msg={msg} />
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
        <table className="data">
          <thead><tr><th>User</th><th>Role</th><th>Signed in</th><th>Last active</th><th>Address</th><th>Browser</th><th></th></tr></thead>
          <tbody>
            {(data?.sessions || []).map((s) => (
              <tr key={s.id}>
                <td>{s.email}{s.current && <span className="chip" style={{ marginLeft: 6 }}>this session</span>}</td>
                <td>{ROLE_LABEL[s.role]}</td><td>{when(s.created_at)}</td><td>{when(s.last_seen_at)}</td>
                <td>{s.ip || '–'}</td><td className="truncate" title={s.user_agent || ''}>{s.user_agent || '–'}</td>
                <td><button type="button" className="btn" disabled={s.current} onClick={() => revoke(s)}>Sign out</button></td>
              </tr>
            ))}
            {data && !data.sessions.length && <tr><td colSpan={7} className="muted">Nobody is signed in.</td></tr>}
            {!data && !error && <tr><td colSpan={7} className="muted">Loading…</td></tr>}
          </tbody>
        </table>
      </div>
      <Pager offset={offset} total={data?.total ?? 0} onChange={setOffset} />
    </section>
  );
}

// ---------------------------------------------------------------- audit log
const AUDIT_ACTIONS = ['login', 'login_fail', 'logout', 'register', 'access_denied', 'user_create', 'user_update', 'user_delete',
  'password_change', 'password_reset_request', 'password_reset_link', 'password_reset', 'session_revoke', 'settings_update',
  'sim_start', 'sim_stop', 'driver_toggle', 'driver_release', 'gui_toggle', 'capacity_reject'];

function Audit() {
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState('');
  const { data, error, reload } = useLoader(() => api.audit(PAGE, offset, action), [offset, action]);
  return (
    <section className="card stack" aria-labelledby="audit-h">
      <div className="card-head">
        <h2 id="audit-h">Audit log</h2>
        <span className="row">
          <label className="small">Action{' '}
            <select value={action} onChange={(e) => { setOffset(0); setAction(e.target.value); }}>
              <option value="">all</option>
              {AUDIT_ACTIONS.map((a) => <option key={a} value={a}>{a.replaceAll('_', ' ')}</option>)}
            </select>
          </label>
          <button type="button" className="btn" onClick={reload}>Refresh</button>
        </span>
      </div>
      {error && <div className="notice error" role="alert">{error}</div>}
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
        <table className="data">
          <thead><tr><th>Time</th><th>User</th><th>Action</th><th>Target</th><th>Detail</th><th>Address</th></tr></thead>
          <tbody>
            {(data?.entries || []).map((e) => (
              <tr key={e.id}>
                <td>{when(e.at)}</td><td>{e.user_email || '–'}</td><td>{e.action.replaceAll('_', ' ')}</td>
                <td>{e.target || '–'}</td><td>{e.detail || '–'}</td><td>{e.ip || '–'}</td>
              </tr>
            ))}
            {data && !data.entries.length && <tr><td colSpan={6} className="muted">No entries.</td></tr>}
            {!data && !error && <tr><td colSpan={6} className="muted">Loading…</td></tr>}
          </tbody>
        </table>
      </div>
      {!action && <Pager offset={offset} total={data?.total ?? 0} onChange={setOffset} />}
    </section>
  );
}

// ---------------------------------------------------------------- settings and system
function Settings() {
  const { data, error, reload } = useLoader(() => api.settings(), []);
  const { refresh } = useAuth();
  const [msg, setMsg] = useState(null);
  async function toggle() {
    setMsg(null);
    try {
      const res = await api.saveSettings({ allow_registration: !data.allow_registration });
      setMsg({ text: res.allow_registration ? 'Visitors can now create viewer accounts.' : 'Registration is closed.' });
      await reload();
      refresh();
    } catch (err) {
      setMsg({ error: true, text: err.message });
    }
  }
  return (
    <section className="card stack" aria-labelledby="set-h">
      <div className="card-head"><h2 id="set-h">Settings</h2></div>
      {error && <div className="notice error" role="alert">{error}</div>}
      <div className="row">
        <span>Self-registration: <strong>{data ? (data.allow_registration ? 'open' : 'closed') : '…'}</strong></span>
        <button type="button" className="btn" disabled={!data} onClick={toggle}>
          {data?.allow_registration ? 'Close registration' : 'Open registration'}
        </button>
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        When open, anyone who reaches the sign-in page can create a viewer account. Researcher and
        administrator roles are always given here, by an administrator.
      </p>
      <Message msg={msg} />
    </section>
  );
}

function Tile({ k, v, sub }) {
  return <div className="tile"><div className="k">{k}</div><div className="v num">{v}</div>{sub && <div className="sub">{sub}</div>}</div>;
}

function SystemHealth() {
  const { data, error, reload } = useLoader(() => api.system(), []);
  useEffect(() => {
    const t = setInterval(reload, 5000);
    return () => clearInterval(t);
  }, [reload]);
  const s = data;
  return (
    <section className="card stack" aria-labelledby="sys-h">
      <div className="card-head"><h2 id="sys-h">System health</h2><span className="small muted">refreshes every 5 s</span></div>
      {error && <div className="notice error" role="alert">{error}</div>}
      {s && (
        <>
          <div className="tiles">
            <Tile k="Live connections" v={`${s.viewers} / ${s.max_connections}`} sub={`${s.capacity_rejected} refused at the limit`} />
            <Tile k="Simulation" v={s.simulation.lifecycle} sub={s.simulation.mock ? 'mock simulation' : `SUMO · ${s.simulation.failures} failed in a row`} />
            <Tile k="Database" v={s.database.ok ? 'OK' : 'Unavailable'} sub={s.database.ok ? `${s.database.users} users · ${s.database.runs} runs` : s.database.error} />
            <Tile k="Signed-in sessions" v={s.database.sessions ?? '–'} />
            <Tile k="Memory (RSS)" v={`${s.memory_mb.rss} MB`} sub={`heap ${s.memory_mb.heapUsed} MB`} />
            <Tile k="Event-loop lag" v={`${s.event_loop_ms.mean} ms`} sub={`p99 ${s.event_loop_ms.p99} ms · last minute`} />
            <Tile k="Uptime" v={`${Math.round(s.uptime_s / 60)} min`} sub={`Node ${s.node} · ${s.env}`} />
            <Tile k="Live stream" v={`${s.live.states_broadcast} states`} sub={`${s.live.bad_lines} bad lines · ${s.live.runs_started} runs started`} />
          </div>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
            <table className="data">
              <thead><tr><th>Database migration</th><th>Applied</th></tr></thead>
              <tbody>
                {(s.database.migrations || []).map((m) => <tr key={m.version}><td>{m.version}. {m.name}</td><td>{when(m.applied_at)}</td></tr>)}
              </tbody>
            </table>
          </div>
        </>
      )}
      {!s && !error && <p className="muted">Loading…</p>}
    </section>
  );
}

const TABS = [
  { id: 'users', title: 'Users', el: <Users /> },
  { id: 'sessions', title: 'Sessions', el: <Sessions /> },
  { id: 'audit', title: 'Audit log', el: <Audit /> },
  { id: 'system', title: 'System', el: <><SystemHealth /><Settings /></> },
];

export default function Administration() {
  const [tab, setTab] = useState('users');
  return (
    <div className="stack">
      <div className="tabs" role="tablist" aria-label="Administration">
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={`tab${tab === t.id ? ' active' : ''}`}
            onClick={() => setTab(t.id)}>{t.title}</button>
        ))}
      </div>
      <div className="stack" role="tabpanel">{TABS.find((t) => t.id === tab).el}</div>
    </div>
  );
}
