// The signed-in user's own account: name and password.
import { useState } from 'react';
import { api } from '../api.js';
import { ROLE_LABEL, useAuth } from '../hooks/useAuth.js';

const MIN_PASSWORD = 10;

export default function Account() {
  const { user, setUser } = useAuth();
  const [name, setName] = useState(user.name);
  const [nameMsg, setNameMsg] = useState(null);
  const [pw, setPw] = useState({ current: '', next: '', again: '' });
  const [pwMsg, setPwMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  async function saveName(e) {
    e.preventDefault();
    setNameMsg(null);
    try {
      const res = await api.updateProfile(name);
      setUser(res.user);
      setNameMsg({ text: 'Name saved.' });
    } catch (err) {
      setNameMsg({ error: true, text: err.body?.issues?.[0]?.message || err.message });
    }
  }

  async function savePassword(e) {
    e.preventDefault();
    setBusy(true);
    setPwMsg(null);
    try {
      await api.changePassword(pw.current, pw.next);
      setPw({ current: '', next: '', again: '' });
      setPwMsg({ text: 'Password changed. Your other sessions were signed out.' });
    } catch (err) {
      setPwMsg({ error: true, text: err.body?.issues?.[0]?.message || err.message });
    } finally {
      setBusy(false);
    }
  }

  const setP = (k) => (e) => setPw((p) => ({ ...p, [k]: e.target.value }));
  const mismatch = pw.again && pw.next !== pw.again;

  return (
    <div className="stack" style={{ maxWidth: 560 }}>
      <section className="card stack" aria-labelledby="acc-h">
        <div className="card-head"><h2 id="acc-h">Your account</h2><span className="chip">{ROLE_LABEL[user.role]}</span></div>
        <p className="muted" style={{ margin: 0 }}>Signed in as {user.email}</p>
        <form className="stack" onSubmit={saveName}>
          <label className="field">Name
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required autoComplete="name" />
          </label>
          <div className="row">
            <button type="submit" className="btn primary" disabled={!name.trim() || name.trim() === user.name}>Save name</button>
          </div>
          {nameMsg && <div className={`notice${nameMsg.error ? ' error' : ''}`} role="status">{nameMsg.text}</div>}
        </form>
      </section>

      <section className="card stack" aria-labelledby="pw-h">
        <div className="card-head"><h2 id="pw-h">Change password</h2></div>
        <form className="stack" onSubmit={savePassword}>
          <label className="field">Current password
            <input type="password" autoComplete="current-password" value={pw.current} onChange={setP('current')} required />
          </label>
          <label className="field">New password
            <input type="password" autoComplete="new-password" value={pw.next} onChange={setP('next')} minLength={MIN_PASSWORD} required />
            <span className="small muted">At least {MIN_PASSWORD} characters</span>
          </label>
          <label className="field">Repeat new password
            <input type="password" autoComplete="new-password" value={pw.again} onChange={setP('again')} required aria-invalid={mismatch ? 'true' : undefined} />
            {mismatch && <span className="small bad">The passwords are not the same</span>}
          </label>
          <div className="row">
            <button type="submit" className="btn primary"
              disabled={busy || !pw.current || pw.next.length < MIN_PASSWORD || pw.next !== pw.again}>
              {busy ? 'Saving…' : 'Change password'}
            </button>
          </div>
          {pwMsg && <div className={`notice${pwMsg.error ? ' error' : ''}`} role="status">{pwMsg.text}</div>}
        </form>
      </section>
    </div>
  );
}
