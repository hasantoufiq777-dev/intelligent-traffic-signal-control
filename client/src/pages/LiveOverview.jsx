import EventLog from '../components/EventLog.jsx';
import JunctionCard from '../components/JunctionCard.jsx';
import LiveCharts from '../components/LiveCharts.jsx';
import NowPlaying from '../components/NowPlaying.jsx';
import { NavPanel, VanetPanel } from '../components/VanetNavPanels.jsx';
import { useLive } from '../hooks/useLiveSocket.js';

// Grid layout matches the map: J2 J3 on top (north), J0 J1 below
const ORDER = ['J2', 'J3', 'J0', 'J1'];

export default function LiveOverview() {
  const live = useLive();
  const tls = live.latest?.tls || {};
  const ids = ORDER.filter((id) => tls[id]).concat(Object.keys(tls).filter((id) => !ORDER.includes(id)));
  const maxQueue = Math.max(10, ...ids.flatMap((id) => Object.values(tls[id]?.queues || {})));

  return (
    <>
      <NowPlaying nowPlaying={live.nowPlaying} latest={live.latest} viewers={live.viewers} status={live.status} />
      {!live.latest && <div className="notice" style={{ marginBottom: 16 }}>Waiting for the simulation to send data…</div>}
      <div className="stack">
        <div className="overview-grid">
          <section aria-label="Junctions">
            <div className="junction-grid">
              {ids.map((id) => <JunctionCard key={id} id={id} data={tls[id]} ev={live.latest?.ev} maxQueue={maxQueue} />)}
            </div>
          </section>
          <EventLog events={live.events} />
        </div>
        <LiveCharts states={live.states} />
        <div className="overview-grid">
          <VanetPanel comm={live.latest?.comm} states={live.states} />
          <NavPanel nav={live.latest?.nav} states={live.states} />
        </div>
      </div>
    </>
  );
}
