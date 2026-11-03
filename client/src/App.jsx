import { lazy, Suspense, useEffect, useState } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';
import { ConnectionBadge } from './components/NowPlaying.jsx';
import SumoGuiButton from './components/SumoGuiButton.jsx';
import { LiveProvider, useLive } from './hooks/useLiveSocket.js';
import UserPanel from './pages/UserPanel.jsx';

// Chart-heavy pages load on demand, so the user panel (the default page) stays small
const LiveOverview = lazy(() => import('./pages/LiveOverview.jsx'));
const Results = lazy(() => import('./pages/Results.jsx'));
const Admin = lazy(() => import('./pages/Admin.jsx'));

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

function TopBar() {
  const { connection } = useLive();
  const [theme, setTheme] = useTheme();
  const next = { auto: 'light', light: 'dark', dark: 'auto' };
  return (
    <header className="topbar">
      <span className="brand"><span aria-hidden="true">🚦</span> VANET Traffic</span>
      <nav className="nav" aria-label="Main">
        <NavLink to="/" end>My drive</NavLink>
        <NavLink to="/live">Live overview</NavLink>
        <NavLink to="/results">Results</NavLink>
      </nav>
      <div className="topbar-right">
        <SumoGuiButton />
        <ConnectionBadge connection={connection} />
        <button type="button" className="icon-btn" onClick={() => setTheme(next[theme])}
          aria-label={`Theme: ${theme}. Switch to ${next[theme]}`}>
          {theme === 'dark' ? '🌙' : theme === 'light' ? '☀️' : '🖥'} {theme}
        </button>
      </div>
    </header>
  );
}

export default function App() {
  return (
    <LiveProvider>
      <TopBar />
      <main>
        <Suspense fallback={<div className="notice">Loading…</div>}>
        <Routes>
          <Route path="/" element={<UserPanel />} />
          <Route path="/live" element={<LiveOverview />} />
          <Route path="/results" element={<Results />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="*" element={<div className="notice">Page not found.</div>} />
        </Routes>
        </Suspense>
      </main>
    </LiveProvider>
  );
}
