// Sign-in, registration (when an administrator has opened it), "forgot password" and the page a
// reset link opens. Shown instead of the application while nobody is signed in.
import { useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api.js';
import { useAuth } from '../hooks/useAuth.js';

const MIN_PASSWORD = 10;

function AuthShell({ title, lead, children, footer }) {
  return (
    <div className="auth-page">
      <section className="card auth-card" aria-labelledby="auth-h">
        <div className="auth-brand"><span aria-hidden="true">🚦</span> VANET Traffic</div>
        <h1 id="auth-h">{title}</h1>
        {lead && <p className="muted" style={{ margin: 0 }}>{lead}</p>}
        {children}
        {footer && <div className="auth-links small">{footer}</div>}
      </section>
      <p className="muted small auth-note">
        VANET-based intelligent traffic signal control: a research platform with a live SUMO simulation.
      </p>
    </div>
  );
}

function Field({ label, hint, error, ...input }) {
  const id = `f-${input.name}`;
  return (
    <label className="field" htmlFor={id}>{label}
      <input id={id} aria-invalid={error ? 'true' : undefined} aria-describedby={hint || error ? `${id}-h` : undefined} {...input} />
      {(error || hint) && <span id={`${id}-h`} className={`small ${error ? 'bad' : 'muted'}`}>{error || hint}</span>}
    </label>
  );
}

/** Field messages from the gateway's validation ({issues: [{path, message}]}). */
const fieldErrors = (err) => Object.fromEntries((err.body?.issues || []).map((i) => [i.path, i.message]));

function useSubmit(action) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [fields, setFields] = useState({});
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setFields({});
    try {
      await action();
    } catch (err) {
      setFields(fieldErrors(err));
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, fields, submit };
}

export function Login() {
  const auth = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const { busy, error, fields, submit } = useSubmit(() => auth.login(email, password));
  return (
    <AuthShell title="Sign in" lead="Sign in to watch the live traffic simulation and the research results."
      footer={(
        <>
          <Link to="/forgot-password">Forgot your password?</Link>
          {auth.registration && <Link to="/register">Create an account</Link>}
        </>
      )}>
      <form className="stack" onSubmit={submit} noValidate>
        <Field label="Email" name="email" type="email" autoComplete="username" required autoFocus value={email}
          onChange={(e) => setEmail(e.target.value)} error={fields.email} />
        <Field label="Password" name="password" type="password" autoComplete="current-password" required value={password}
          onChange={(e) => setPassword(e.target.value)} error={fields.password} />
        {error && <div className="notice error" role="alert">{error}</div>}
        <button type="submit" className="btn primary" disabled={busy || !email || !password}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </AuthShell>
  );
}

export function Register() {
  const auth = useAuth();
  const [form, setForm] = useState({ name: '', email: '', password: '', again: '' });
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const mismatch = form.again && form.password !== form.again;
  const { busy, error, fields, submit } = useSubmit(() => auth.register(form.email, form.name, form.password));
  if (!auth.registration) return <Navigate to="/login" replace />;
  return (
    <AuthShell title="Create an account" lead="New accounts can watch the live simulation and the results."
      footer={<Link to="/login">Already have an account? Sign in</Link>}>
      <form className="stack" onSubmit={submit} noValidate>
        <Field label="Name" name="name" autoComplete="name" required value={form.name} onChange={set('name')} error={fields.name} />
        <Field label="Email" name="email" type="email" autoComplete="username" required value={form.email} onChange={set('email')} error={fields.email} />
        <Field label="Password" name="password" type="password" autoComplete="new-password" required minLength={MIN_PASSWORD}
          value={form.password} onChange={set('password')} error={fields.password} hint={`At least ${MIN_PASSWORD} characters`} />
        <Field label="Repeat password" name="again" type="password" autoComplete="new-password" required value={form.again}
          onChange={set('again')} error={mismatch ? 'The passwords are not the same' : null} />
        {error && <div className="notice error" role="alert">{error}</div>}
        <button type="submit" className="btn primary"
          disabled={busy || !form.name || !form.email || form.password.length < MIN_PASSWORD || form.password !== form.again}>
          {busy ? 'Creating…' : 'Create account'}
        </button>
      </form>
    </AuthShell>
  );
}

export function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(null);
  const { busy, error, fields, submit } = useSubmit(async () => setSent((await api.forgotPassword(email)).message));
  return (
    <AuthShell title="Forgot your password?"
      lead="Enter your email. An administrator will be notified and will send you a link to choose a new password."
      footer={<Link to="/login">Back to sign in</Link>}>
      {sent ? <div className="notice" role="status">{sent}</div> : (
        <form className="stack" onSubmit={submit} noValidate>
          <Field label="Email" name="email" type="email" autoComplete="username" required autoFocus value={email}
            onChange={(e) => setEmail(e.target.value)} error={fields.email} />
          {error && <div className="notice error" role="alert">{error}</div>}
          <button type="submit" className="btn primary" disabled={busy || !email}>{busy ? 'Sending…' : 'Ask for a reset link'}</button>
        </form>
      )}
    </AuthShell>
  );
}

export function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get('token') || '';
  const [password, setPassword] = useState('');
  const [again, setAgain] = useState('');
  const [done, setDone] = useState(false);
  const mismatch = again && password !== again;
  const { busy, error, fields, submit } = useSubmit(async () => {
    await api.resetPassword(token, password);
    setDone(true);
  });
  if (!token) {
    return (
      <AuthShell title="Reset link missing" footer={<Link to="/login">Back to sign in</Link>}>
        <div className="notice error" role="alert">This page needs the reset link an administrator sent you.</div>
      </AuthShell>
    );
  }
  return (
    <AuthShell title="Choose a new password" footer={<Link to="/login">Back to sign in</Link>}>
      {done ? (
        <>
          <div className="notice" role="status">Your password was changed. Sign in with the new password.</div>
          <button type="button" className="btn primary" onClick={() => navigate('/login', { replace: true })}>Go to sign in</button>
        </>
      ) : (
        <form className="stack" onSubmit={submit} noValidate>
          <Field label="New password" name="password" type="password" autoComplete="new-password" required autoFocus
            minLength={MIN_PASSWORD} value={password} onChange={(e) => setPassword(e.target.value)}
            error={fields.password} hint={`At least ${MIN_PASSWORD} characters`} />
          <Field label="Repeat password" name="again" type="password" autoComplete="new-password" required value={again}
            onChange={(e) => setAgain(e.target.value)} error={mismatch ? 'The passwords are not the same' : null} />
          {error && <div className="notice error" role="alert">{error}</div>}
          <button type="submit" className="btn primary" disabled={busy || password.length < MIN_PASSWORD || password !== again}>
            {busy ? 'Saving…' : 'Set new password'}
          </button>
        </form>
      )}
    </AuthShell>
  );
}
