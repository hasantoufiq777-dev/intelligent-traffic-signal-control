// Live traffic: the map is the centre of the page; the side panel shows the network overview, the
// selected junction, the selected vehicle (and driving), and eco-driving.
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import DrivePanel from '../components/DrivePanel.jsx';
import EcoSummary from '../components/EcoSummary.jsx';
import EventLog from '../components/EventLog.jsx';
import JunctionPanel from '../components/JunctionPanel.jsx';
import MyCarPanel from '../components/MyCarPanel.jsx';
import { AmbulanceList, IncidentList, StatusStrip } from '../components/NetworkStatus.jsx';
import NowPlaying from '../components/NowPlaying.jsx';
import TrafficMap from '../components/TrafficMap.jsx';
import ZonePanel from '../components/ZonePanel.jsx';
import { useLive } from '../hooks/useLiveSocket.js';
import { FLAG } from '../lib/vehicles.js';

const USER_EVENTS = new Set(['preempt_start', 'preempt_end', 'incident_start', 'incident_detected', 'incident_end', 'ev_reroute',
  'reroute', 'driver_start', 'driver_end', 'zone_activated', 'zone_guided', 'zone_ev_priority', 'zone_deactivated']);
const ORDER = ['J2', 'J3', 'J0', 'J1']; // as on the map: J2 J3 north, J0 J1 south
const TABS = [['overview', 'Overview'], ['junction', 'Junction'], ['vehicle', 'Vehicle'], ['eco', 'Eco-driving']];

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
  const [tab, setTab] = useState('overview');
  const [junction, setJunction] = useState(null);

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
            <span className="small muted">
              {rows.length} vehicles{latest?.t != null ? ` · t = ${latest.t} s` : ''} · select a car or a junction
            </span>
          </div>
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
          />
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
            {tab === 'eco' && <EcoSummary eco={latest?.eco} />}
          </div>
        </div>
      </div>
    </>
  );
}
