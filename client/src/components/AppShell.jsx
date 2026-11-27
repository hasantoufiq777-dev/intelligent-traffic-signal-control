// Application frame for signed-in users: collapsible sidebar (a drawer on small screens), top bar
// with the page title, connection state, theme and the user menu.
import { useEffect, useRef, useState } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { IMPORTANT_EVENTS } from '../lib/events.js';
import { ROLE_LABEL, useAuth } from '../hooks/useAuth.js';
import { useInstallPrompt } from '../hooks/useInstallPrompt.js';
import { useLive } from '../hooks/useLiveSocket.js';
import Icon from './Icon.jsx';
import { ConnectionBadge } from './NowPlaying.jsx';
import SumoGuiButton from './SumoGuiButton.jsx';

const THEME_KEY = 'itsc-theme';
const SIDEBAR_KEY = 'itsc-sidebar';

function stored(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}
function store(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* per-viewer convenience only */
  }
}

export function useTheme() {
  const [theme, setTheme] = useState(() => stored(THEME_KEY, 'dark'));
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'auto') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', theme);
    store(THEME_KEY, theme);
  }, [theme]);
  return [theme, setTheme];
}

export function ThemeButton() {
  const [theme, setTheme] = useTheme();
  const next = { dark: 'light', light: 'auto', auto: 'dark' };
  const label = { dark: 'Dark', light: 'Light', auto: 'System' };
  return (
    <button type="button" className="icon-btn" onClick={() => setTheme(next[theme] || 'dark')}
      aria-label={`Theme: ${label[theme]}. Switch to ${label[next[theme]]}`} title={`Theme: ${label[theme]}`}>
      <Icon name={theme === 'dark' ? 'moon' : theme === 'light' ? 'sun' : 'monitor'} size={16} />
      <span className="hide-sm">{label[theme]}</span>
    </button>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();
  const { canInstall, install } = useInstallPrompt(); // "Install app" where the browser offers it
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
      <button type="button" className="btn user-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="avatar" aria-hidden="true">{user.name.trim().charAt(0).toUpperCase()}</span>
        <span className="vh-sm">{user.name} <span className="small muted">· {ROLE_LABEL[user.role]}</span></span>
      </button>
      {open && (
        <div className="menu-list" role="menu">
          <div className="small muted menu-info">{user.name}<br />{user.email}<br />{ROLE_LABEL[user.role]}</div>
          <Link role="menuitem" to="/account" onClick={() => setOpen(false)}>Account</Link>
          {canInstall && (
            <button type="button" role="menuitem" onClick={() => { setOpen(false); install(); }}>Install app</button>
          )}
          <button type="button" role="menuitem" onClick={logout}>Sign out</button>
        </div>
      )}
    </div>
  );
}

/** Navigation, grouped; items the role may not use are left out (the gateway enforces access). */
export function navGroups({ isAdmin, canControl }) {
  return [
    { title: 'Monitor', items: [
      { to: '/', end: true, icon: 'map', label: 'Live traffic' },
      { to: '/live', icon: 'gauge', label: 'Live overview' },
      { to: '/signals', icon: 'signal', label: 'Signals' },
      { to: '/vanet', icon: 'wifi', label: 'VANET' },
      { to: '/emergency', icon: 'ambulance', label: 'Emergency' },
      { to: '/navigation', icon: 'route', label: 'Navigation' },
      { to: '/incidents', icon: 'alert', label: 'Incidents' },
      { to: '/work-zone', icon: 'cone', label: 'Work zone' },
      { to: '/eco', icon: 'leaf', label: 'Eco driving' },
      { to: '/glosa', icon: 'car', label: 'Driving' },
      { to: '/events', icon: 'bell', label: 'Events' },
    ] },
    // hidden: not in the sidebar, but the page still opens from a link (and keeps its title)
    { title: 'Research', items: [
      { to: '/demo', icon: 'flag', label: 'Guided demo', hidden: true },
      { to: '/results', icon: 'chart', label: 'Results', hidden: true },
      { to: '/ga', icon: 'dna', label: 'GA optimisation' },
      { to: '/runs', icon: 'list', label: 'Run history', hidden: true },
      { to: '/docs', icon: 'book', label: 'Documentation' },
    ] },
    canControl && { title: 'Control', items: [{ to: '/admin', icon: 'play', label: 'Run control' }] },
    isAdmin && { title: 'System', items: [{ to: '/administration', icon: 'shield', label: 'Administration' }] },
  ].filter(Boolean);
}

export default function AppShell({ children }) {
  const auth = useAuth();
  const { connection, connectionNote, events, nowPlaying } = useLive();
  const location = useLocation();
  // Bell: important events of the current run that arrived since the Events page was last open
  const runId = nowPlaying?.current?.run_id ?? null;
  const important = events.filter((e) => IMPORTANT_EVENTS.has(e.kind)).length;
  const [seen, setSeen] = useState({ run: null, n: 0 });
  const onEvents = location.pathname === '/events';
  useEffect(() => {
    if (onEvents || seen.run !== runId) setSeen({ run: runId, n: onEvents ? important : 0 });
  }, [onEvents, runId, important, seen.run]);
  const unread = seen.run === runId ? Math.max(0, important - seen.n) : important;
  const [collapsed, setCollapsed] = useState(() => stored(SIDEBAR_KEY, 'open') === 'collapsed');
  const [drawer, setDrawer] = useState(false);
  const groups = navGroups(auth);
  const items = groups.flatMap((g) => g.items);
  const current = items.find((i) => location.pathname === i.to || (!i.end && location.pathname.startsWith(`${i.to}/`)))
    || (location.pathname === '/account' ? { label: 'Account' } : null);

  useEffect(() => { setDrawer(false); }, [location.pathname]); // navigating closes the mobile drawer

  // Each page returns to where it was scrolled. Saving starts once the position is restored, because
  // the browser moves the scroll position while the new page is still loading.
  useEffect(() => {
    if (location.hash) return undefined;
    const key = `itsc-scroll:${location.pathname}`;
    let target = 0;
    try {
      target = Number(sessionStorage.getItem(key)) || 0;
    } catch {
      /* start at the top */
    }
    let saving = false;
    let frames = 0;
    let raf = 0;
    const restore = () => {
      const fits = document.documentElement.scrollHeight - window.innerHeight >= target;
      if (fits || frames > 90) {
        window.scrollTo(0, Math.min(target, document.documentElement.scrollHeight - window.innerHeight));
        saving = true;
        return;
      }
      frames += 1;
      raf = requestAnimationFrame(restore);
    };
    const save = () => {
      if (!saving) return;
      try {
        sessionStorage.setItem(key, String(Math.round(window.scrollY)));
      } catch {
        /* convenience only */
      }
    };
    raf = requestAnimationFrame(restore);
    window.addEventListener('scroll', save, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('scroll', save);
    };
  }, [location.pathname, location.hash]);
  useEffect(() => {
    if (!drawer) return undefined;
    const esc = (e) => { if (e.key === 'Escape') setDrawer(false); };
    document.addEventListener('keydown', esc);
    return () => document.removeEventListener('keydown', esc);
  }, [drawer]);

  const toggleCollapsed = () => setCollapsed((c) => {
    store(SIDEBAR_KEY, c ? 'open' : 'collapsed');
    return !c;
  });

  return (
    <div className={`shell${collapsed ? ' collapsed' : ''}${drawer ? ' drawer-open' : ''}`}>
      <a className="skip-link" href="#main">Skip to content</a>
      <aside className="sidebar" aria-label="Main navigation">
        <div className="sidebar-head">
          <span className="brand"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
            <span className="brand-text">VANET Traffic</span></span>
          <button type="button" className="icon-btn only-mobile" onClick={() => setDrawer(false)} aria-label="Close navigation">
            <Icon name="close" />
          </button>
        </div>
        <nav>
          {groups.map((g) => (
            <div className="nav-group" key={g.title}>
              <div className="nav-title">{g.title}</div>
              {g.items.filter((i) => !i.hidden).map((i) => (
                <NavLink key={i.to} to={i.to} end={i.end} className="nav-item" title={collapsed ? i.label : undefined}>
                  <Icon name={i.icon} /><span className="nav-label">{i.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <button type="button" className="nav-item collapse-btn only-desktop" onClick={toggleCollapsed}
          aria-label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'} aria-expanded={!collapsed}>
          <Icon name="chevron" className={collapsed ? '' : 'flip'} /><span className="nav-label">Collapse</span>
        </button>
      </aside>
      <button type="button" className="drawer-backdrop" aria-label="Close navigation" tabIndex={drawer ? 0 : -1} onClick={() => setDrawer(false)} />

      <div className="shell-main">
        <header className="topbar">
          <button type="button" className="icon-btn only-mobile" onClick={() => setDrawer(true)} aria-label="Open navigation"
            aria-expanded={drawer}>
            <Icon name="menu" />
          </button>
          <h1 className="page-title">{current?.label ?? 'VANET Traffic'}</h1>
          <div className="topbar-right">
            <span className="only-desktop sumo-gui"><SumoGuiButton /></span>
            <ConnectionBadge connection={connection} />
            <Link to="/events" className="icon-btn bell" aria-label={unread ? `${unread} new important events. Open the event centre` : 'Open the event centre'}>
              <Icon name="bell" size={17} />{unread > 0 && <span className="badge" aria-hidden="true">{unread > 99 ? '99+' : unread}</span>}
            </Link>
            <ThemeButton />
            <UserMenu />
          </div>
        </header>
        {connection === 'capacity' && (
          <div className="notice error page-notice" role="alert">{connectionNote}</div>
        )}
        <main id="main" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
}
