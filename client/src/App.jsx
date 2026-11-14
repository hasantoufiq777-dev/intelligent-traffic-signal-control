import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import AppShell, { ThemeButton } from './components/AppShell.jsx';
import { useAuth } from './hooks/useAuth.js';
import { LiveProvider } from './hooks/useLiveSocket.js';
import { ForgotPassword, Login, Register, ResetPassword } from './pages/AuthPages.jsx';
import UserPanel from './pages/UserPanel.jsx';

// Chart-heavy and role-specific pages load on demand, so the map page (the default) stays small
const LiveOverview = lazy(() => import('./pages/LiveOverview.jsx'));
const Results = lazy(() => import('./pages/Results.jsx'));
const Admin = lazy(() => import('./pages/Admin.jsx'));
const Administration = lazy(() => import('./pages/Administration.jsx'));
const Account = lazy(() => import('./pages/Account.jsx'));
const Docs = lazy(() => import('./pages/Docs.jsx'));
const Demo = lazy(() => import('./pages/Demo.jsx'));
const modulePage = (name) => lazy(() => import('./pages/Modules.jsx').then((m) => ({ default: m[name] })));
const researchPage = (name) => lazy(() => import('./pages/Research.jsx').then((m) => ({ default: m[name] })));
const Signals = modulePage('Signals');
const Vanet = modulePage('Vanet');
const Emergency = modulePage('Emergency');
const Navigation = modulePage('Navigation');
const Incidents = modulePage('Incidents');
const WorkZone = modulePage('WorkZone');
const Glosa = modulePage('Glosa');
const GaPage = researchPage('GaPage');
const Runs = researchPage('Runs');
const EventsPage = researchPage('EventsPage');

/** Pages for a role the user does not have are not rendered (the gateway refuses the data anyway). */
function Restricted({ allow, children }) {
  return allow ? children : <div className="notice error" role="alert">You do not have permission to open this page.</div>;
}

function SignedIn() {
  const { isAdmin, canControl } = useAuth();
  return (
    <LiveProvider>
      <AppShell>
        <Suspense fallback={<div className="notice" role="status">Loading…</div>}>
          <Routes>
            <Route path="/" element={<UserPanel />} />
            <Route path="/live" element={<LiveOverview />} />
            <Route path="/signals" element={<Signals />} />
            <Route path="/vanet" element={<Vanet />} />
            <Route path="/emergency" element={<Emergency />} />
            <Route path="/navigation" element={<Navigation />} />
            <Route path="/incidents" element={<Incidents />} />
            <Route path="/work-zone" element={<WorkZone />} />
            <Route path="/glosa" element={<Glosa />} />
            <Route path="/events" element={<EventsPage />} />
            <Route path="/results" element={<Results />} />
            <Route path="/ga" element={<GaPage />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/docs" element={<Docs />} />
            <Route path="/demo" element={<Demo />} />
            <Route path="/admin" element={<Restricted allow={canControl}><Admin /></Restricted>} />
            <Route path="/administration" element={<Restricted allow={isAdmin}><Administration /></Restricted>} />
            <Route path="/account" element={<Account />} />
            {['/login', '/register', '/forgot-password', '/reset-password'].map((p) => (
              <Route key={p} path={p} element={<Navigate to="/" replace />} />
            ))}
            <Route path="*" element={<div className="notice">Page not found.</div>} />
          </Routes>
        </Suspense>
      </AppShell>
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
