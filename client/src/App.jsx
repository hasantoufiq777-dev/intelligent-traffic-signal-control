import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { Link, Navigate, NavLink, Route, Routes } from 'react-router-dom';
import { ConnectionBadge } from './components/NowPlaying.jsx';
import SumoGuiButton from './components/SumoGuiButton.jsx';
import { ROLE_LABEL, useAuth } from './hooks/useAuth.js';
import { LiveProvider, useLive } from './hooks/useLiveSocket.js';
import { ForgotPassword, Login, Register, ResetPassword } from './pages/AuthPages.jsx';
import UserPanel from './pages/UserPanel.jsx';

// Chart-heavy and role-specific pages load on demand, so the map page (the default) stays small
const LiveOverview = lazy(() => import('./pages/LiveOverview.jsx'));
const Results = lazy(() => import('./pages/Results.jsx'));
const Admin = lazy(() => import('./pages/Admin.jsx'));
const Administration = lazy(() => import('./pages/Administration.jsx'));
const Account = lazy(() => import('./pages/Account.jsx'));

const THEME_KEY = 'itsc-theme';

function useTheme() {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem(THEME_KEY) || 'auto';
    } catch {
      return 'auto';
    }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'auto') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* per-viewer convenience only */
    }
  }, [theme]);
  return [theme, setTheme];
}

function ThemeButton() {
  const [theme, setTheme] = useTheme();
  const next = { auto: 'light', light: 'dark', dark: 'auto' };
  return (
    <button type="button" className="icon-btn" onClick={() => setTheme(next[theme])}
      aria-label={`Theme: ${theme}. Switch to ${next[theme]}`}>
      {theme === 'dark' ? '🌙' : theme === 'light' ? '☀️' : '🖥'} {theme}
    </button>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const esc = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);
  return (
    <div className="menu" ref={ref}>
      <button type="button" className="btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {user.name} <span className="small muted">· {ROLE_LABEL[user.role]}</span>
      </button>
      {open && (
        <div className="menu-list" role="menu">
          <div className="small muted menu-info">{user.email}</div>
          <Link role="menuitem" to="/account" onClick={() => setOpen(false)}>Account</Link>
          <button type="button" role="menuitem" onClick={logout}>Sign out</button>
        </div>
      )}
    </div>
  );
}

function TopBar() {
  const { connection } = useLive();
  const { isAdmin, canControl } = useAuth();
  return (
    <header className="topbar">
      <span className="brand"><span aria-hidden="true">🚦</span> VANET Traffic</span>
      <nav className="nav" aria-label="Main">
        <NavLink to="/" end>My drive</NavLink>
        <NavLink to="/live">Live overview</NavLink>
        <NavLink to="/results">Results</NavLink>
        {canControl && <NavLink to="/admin">Run control</NavLink>}
        {isAdmin && <NavLink to="/administration">Administration</NavLink>}
      </nav>
      <div className="topbar-right">
        <SumoGuiButton />
        <ConnectionBadge connection={connection} />
        <ThemeButton />
        <UserMenu />
      </div>
    </header>
  );
}

/** Pages for a role the user does not have are not rendered (the gateway refuses the data anyway). */
function Restricted({ allow, children }) {
  return allow ? children : <div className="notice error" role="alert">You do not have permission to open this page.</div>;
}

function SignedIn() {
  const { isAdmin, canControl } = useAuth();
  return (
    <LiveProvider>
      <TopBar />
      <main>
        <Suspense fallback={<div className="notice">Loading…</div>}>
          <Routes>
            <Route path="/" element={<UserPanel />} />
            <Route path="/live" element={<LiveOverview />} />
            <Route path="/results" element={<Results />} />
            <Route path="/admin" element={<Restricted allow={canControl}><Admin /></Restricted>} />
            <Route path="/administration" element={<Restricted allow={isAdmin}><Administration /></Restricted>} />
            <Route path="/account" element={<Account />} />
            {['/login', '/register', '/forgot-password', '/reset-password'].map((p) => (
              <Route key={p} path={p} element={<Navigate to="/" replace />} />
            ))}
            <Route path="*" element={<div className="notice">Page not found.</div>} />
          </Routes>
        </Suspense>
      </main>
    </LiveProvider>
  );
}

function SignedOut() {
  return (
    <>
      <div className="auth-theme"><ThemeButton /></div>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </>
  );
}

export default function App() {
  const auth = useAuth();
  if (auth.status === 'loading') return <div className="notice splash" role="status">Loading…</div>;
  if (auth.status === 'unreachable') {
    return (
      <div className="notice error splash" role="alert">
        <p style={{ marginTop: 0 }}>The server cannot be reached. {auth.error}</p>
        <button type="button" className="btn" onClick={auth.refresh}>Try again</button>
      </div>
    );
  }
  return auth.user ? <SignedIn key={auth.user.id} /> : <SignedOut />;
}
