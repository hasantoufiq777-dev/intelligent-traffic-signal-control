// Live traffic: the map is the centre of the page, shown as the overhead traffic control view or as a
// 3D driver perspective of the driven or followed vehicle (same simulation frames). The side panel
// shows the network overview, the selected junction, the selected vehicle (and driving), the A* route
// explorer and eco-driving.
import { lazy, Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import DrivePanel from '../components/DrivePanel.jsx';
import EcoOverview from '../components/EcoOverview.jsx';
import EventLog from '../components/EventLog.jsx';
import JunctionPanel from '../components/JunctionPanel.jsx';
import MyCarPanel from '../components/MyCarPanel.jsx';
import { AmbulanceList, IncidentList, StatusStrip } from '../components/NetworkStatus.jsx';
import NowPlaying from '../components/NowPlaying.jsx';
import TrafficMap from '../components/TrafficMap.jsx';
import RouteExplorer from '../components/RouteExplorer.jsx';
import ZonePanel from '../components/ZonePanel.jsx';
import { useLive } from '../hooks/useLiveSocket.js';
import { usePersistentState } from '../hooks/usePersistentState.js';
import { FLAG } from '../lib/vehicles.js';

const USER_EVENTS = new Set(['preempt_start', 'preempt_end', 'incident_start', 'incident_detected', 'incident_end', 'ev_reroute',
  'reroute', 'driver_start', 'driver_end', 'zone_activated', 'zone_guided', 'zone_ev_priority', 'zone_deactivated']);
const ORDER = ['J2', 'J3', 'J0', 'J1']; // as on the map: J2 J3 north, J0 J1 south
const TABS = [['overview', 'Overview'], ['junction', 'Junction'], ['vehicle', 'Vehicle'], ['routes', 'Routes']];
// three.js is loaded only when the driver perspective is opened
const DriverView3D = lazy(() => import('../components/DriverView3D.jsx'));
const VIEW_KEY = 'itsc-view';

const STALE = {
  STOPPED: 'Simulation stopped: the map shows the last state of the previous run',
  STOPPING: 'The simulation is stopping',
  STARTING: 'The simulation is starting',
  FAILED: 'The simulation failed: the map shows the last state received',
  RECOVERING: 'The simulation failed and is being restarted',
};

export default function UserPanel() {
  const live = useLive();
  const rows = live.vehicles?.rows || [];
  const [params] = useSearchParams();
  const wanted = params.get('follow'); // demo links: ?follow=eco | ?follow=ambulance | ?follow=<vehicle id>
  const { followId, follow } = live;
  const [tab, setTab] = usePersistentState('live-tab', 'overview');
  const [junction, setJunction] = usePersistentState('live-junction', null);
  const runId = live.nowPlaying?.current?.run_id ?? null;
  const [routeStore, setRouteStore] = usePersistentState('live-routes', null); // route explorer answer, for one run
  const routes = routeStore?.runId === runId ? routeStore.data : null;
  const setRoutes = (data) => setRouteStore(data ? { runId, data } : null);
  const [routeSel, setRouteSel] = usePersistentState('live-route-selected', 0);
  const [view, setViewState] = useState(() => {
    try {
      return localStorage.getItem(VIEW_KEY) === 'driver' ? 'driver' : 'map';
    } catch {
      return 'map';
    }
  });
  const setView = (v) => {
    setViewState(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* per-viewer convenience only */
    }
  };
  const egoId = live.myDrive?.active && live.myDrive.vid ? live.myDrive.vid : live.followId;

  useEffect(() => {
    if (!wanted || followId || !rows.length) return;
    const pickBy = (bit) => rows.find((r) => (r[4] & bit) !== 0)?.[0];
    const id = wanted === 'eco' ? pickBy(FLAG.ECO) : wanted === 'ambulance' ? pickBy(FLAG.EMERGENCY)
      : rows.find((r) => r[0] === wanted)?.[0];
    if (id) follow(id);
  }, [wanted, followId, rows, follow]);

  const latest = live.latest;
  const tls = latest?.tls || {};
  const ids = ORDER.filter((id) => tls[id]).concat(Object.keys(tls).filter((id) => !ORDER.includes(id)));
  const np = live.nowPlaying;
  const running = Boolean(np?.running) && np?.lifecycle !== 'STOPPING';
  const stale = live.connection !== 'connected' ? 'Not connected: the map is not being updated'
    : !running ? STALE[np?.lifecycle] ?? STALE.STOPPED
      : np?.playback?.paused ? `Paused: the map shows simulated second ${latest?.t ?? '–'} until the run is resumed` : null;

  const selectCar = (id) => {
    follow(id);
    if (id) setTab('vehicle');
  };
  const selectJunction = (id) => {
    setJunction(id);
    if (id) setTab('junction');
  };

  return (
    <>
      <NowPlaying nowPlaying={np} latest={latest} viewers={live.viewers} status={live.status} />
      <StatusStrip latest={latest} network={live.network} running={running} />
      <div className="live-layout">
        <section className="card map-card" aria-labelledby="map-h">
          <div className="card-head">
            <h2 id="map-h">Traffic now</h2>
            <div className="view-switch" role="group" aria-label="View">
              <button type="button" className={`btn${view === 'map' ? ' primary' : ''}`} aria-pressed={view === 'map'}
                onClick={() => setView('map')}>Traffic control view</button>
              <button type="button" className={`btn${view === 'driver' ? ' primary' : ''}`} aria-pressed={view === 'driver'}
                onClick={() => setView('driver')}>Driver perspective</button>
            </div>
            <span className="small muted">
              {rows.length} vehicles{latest?.t != null ? ` · t = ${latest.t} s` : ''} · {view === 'map' ? 'select a car or a junction'
                : egoId ? `seen from ${egoId}` : 'select a vehicle first'}
            </span>
          </div>
          {view === 'driver' ? (
            <Suspense fallback={<div className="notice" role="status">Loading the 3D view…</div>}>
              <DriverView3D network={live.network} vehicles={live.vehicles} tlsState={latest?.tls} incidents={latest?.incidents}
                egoId={egoId} detail={live.followed} drive={live.myDrive?.active ? live.myDrive.last : null} />
            </Suspense>
          ) : (
          <TrafficMap
            network={live.network}
            vehicles={live.vehicles}
            tlsState={latest?.tls}
            zoneState={latest?.zone}
            edgeSpeeds={latest?.edge_speeds}
            incidents={latest?.incidents}
            evRoutes={latest?.ev_routes}
            followId={live.followId}
            onSelect={selectCar}
            selectedJunction={junction}
            onSelectJunction={selectJunction}
            stale={stale}
            routeOverlay={routes ? { routes: routes.routes, selected: routeSel } : null}
          />
          )}
        </section>

        <div className="side-panel">
          <div className="tabs" role="tablist" aria-label="Details">
            {TABS.map(([id, title]) => (
              <button key={id} type="button" role="tab" id={`tab-${id}`} aria-selected={tab === id} aria-controls="side-tabpanel"
                className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{title}</button>
            ))}
          </div>
          <div className="stack" role="tabpanel" id="side-tabpanel" aria-labelledby={`tab-${tab}`}>
            {tab === 'overview' && (
              <>
                <EcoOverview eco={latest?.eco} onFollow={selectCar} />
                <ZonePanel zone={latest?.zone} />
                <IncidentList incidents={latest?.incidents} zone={latest?.zone} />
                <AmbulanceList ev={latest?.ev} routes={latest?.ev_routes} onFollow={selectCar} />
                <EventLog events={live.events} limit={10} filter={(k) => USER_EVENTS.has(k)} />
              </>
            )}
            {tab === 'junction' && (
              <JunctionPanel ids={ids} selected={junction} onSelect={setJunction} tlsState={tls} ev={latest?.ev}
                controller={np?.current?.entry?.controller} />
            )}
            {tab === 'vehicle' && (
              <>
                {!live.myDrive?.active && (
                  <MyCarPanel followId={live.followId} detail={live.followed} rows={rows} tlsState={latest?.tls}
                    onFollow={live.follow} events={live.events} />
                )}
                <DrivePanel live={live} />
              </>
            )}
            {tab === 'routes' && (
              <RouteExplorer live={live} result={routes} onResult={(r) => { setRoutes(r); if (r) setView('map'); }}
                selected={routeSel} onSelect={setRouteSel} />
            )}
          </div>
        </div>
      </div>
    </>
  );
}
