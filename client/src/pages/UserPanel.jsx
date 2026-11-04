import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import EcoSummary from '../components/EcoSummary.jsx';
import { FLAG } from '../lib/vehicles.js';
import EventLog from '../components/EventLog.jsx';
import MyCarPanel from '../components/MyCarPanel.jsx';
import NowPlaying from '../components/NowPlaying.jsx';
import TrafficMap from '../components/TrafficMap.jsx';
import { useLive } from '../hooks/useLiveSocket.js';

const USER_EVENTS = new Set(['preempt_start', 'preempt_end', 'incident_start', 'incident_detected', 'incident_end', 'ev_reroute']);

export default function UserPanel() {
  const live = useLive();
  const rows = live.vehicles?.rows || [];
  const [params] = useSearchParams();
  const wanted = params.get('follow'); // demo links: ?follow=eco | ?follow=ambulance | ?follow=<vehicle id>
  const { followId, follow } = live;

  useEffect(() => {
    if (!wanted || followId || !rows.length) return;
    const pickBy = (bit) => rows.find((r) => (r[4] & bit) !== 0)?.[0];
    const id = wanted === 'eco' ? pickBy(FLAG.ECO) : wanted === 'ambulance' ? pickBy(FLAG.EMERGENCY)
      : rows.find((r) => r[0] === wanted)?.[0];
    if (id) follow(id);
  }, [wanted, followId, rows, follow]);
  return (
    <>
      <NowPlaying nowPlaying={live.nowPlaying} latest={live.latest} viewers={live.viewers} status={live.status} />
      <div className="user-layout">
        <section className="card" aria-labelledby="map-h">
          <div className="card-head">
            <h2 id="map-h">Traffic now</h2>
            <span className="small muted">{rows.length} vehicles · tap a car to follow it</span>
          </div>
          <TrafficMap
            network={live.network}
            vehicles={live.vehicles}
            tlsState={live.latest?.tls}
            followId={live.followId}
            onSelect={live.follow}
          />
        </section>
        <div className="stack">
          <MyCarPanel
            followId={live.followId}
            detail={live.followed}
            rows={rows}
            tlsState={live.latest?.tls}
            onFollow={live.follow}
            events={live.events}
          />
          <EcoSummary eco={live.latest?.eco} />
          <EventLog events={live.events} limit={8} filter={(k) => USER_EVENTS.has(k)} />
        </div>
      </div>
    </>
  );
}
