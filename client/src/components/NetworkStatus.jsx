// Network-wide figures of the live run (from the latest state message) and the lists of active
// incidents and ambulances. A dash means the simulation did not send that value.
import { fmt, isNum } from '../lib/format.js';

const KIND_TEXT = { edge_blocked: 'Road closed', lane_blocked: 'Lane blocked' };

/** Network congestion from the share of vehicles standing in a queue at a signal. */
export function congestionLevel(latest) {
  const n = latest?.metrics?.n_vehicles;
  const tls = latest?.tls;
  if (!n || !tls) return null;
  const queued = Object.values(tls).reduce((sum, j) => sum + Object.values(j.queues || {}).reduce((a, b) => a + b, 0), 0);
  const share = queued / n;
  const pct = `${Math.round(share * 100)} % of vehicles queued`;
  if (share >= 0.5) return { text: 'Heavy', cls: 'bad', sub: pct };
  if (share >= 0.25) return { text: 'Moderate', cls: 'warn', sub: pct };
  return { text: 'Light', cls: 'good', sub: pct };
}

function Tile({ k, v, sub, cls }) {
  return (
    <div className="tile">
      <div className="k">{k}</div>
      <div className={`v num${cls ? ` ${cls}` : ''}`}>{v}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function StatusStrip({ latest, network, running }) {
  const m = latest?.metrics;
  const cong = running ? congestionLevel(latest) : null;
  const incidents = latest?.incidents ? new Set([...Object.keys(latest.incidents.actual || {}), ...Object.keys(latest.incidents.known || {})]).size : null;
  const comm = latest?.comm;
  const vanet = network?.vanet?.active;
  const dash = '–';
  return (
    <div className="tiles status-strip" role="group" aria-label="Network status">
      <Tile k="Vehicles on the road" v={m?.n_vehicles ?? dash} />
      <Tile k="Average speed" v={isNum(m?.avg_speed_kmh) ? `${fmt(m.avg_speed_kmh, 0)} km/h` : dash} />
      <Tile k="Average waiting" v={isNum(m?.avg_wait) ? `${fmt(m.avg_wait, 1)} s` : dash} sub="vehicles now on the road" />
      <Tile k="Congestion" v={cong?.text ?? dash} cls={cong?.cls} sub={cong?.sub} />
      <Tile k="Active incidents" v={incidents ?? dash} cls={incidents ? 'bad' : undefined} />
      <Tile k="Ambulances" v={latest?.ev ? latest.ev.length : dash} cls={latest?.ev?.length ? 'bad' : undefined} />
      <Tile k="VANET delivery" v={vanet && isNum(comm?.pdr) ? `${fmt(comm.pdr * 100, 0)} %` : dash}
        sub={vanet ? `${fmt((comm?.penetration ?? network.vanet.penetration) * 100, 0)} % of cars connected` : 'no VANET in this run'} />
      <Tile k="Throughput" v={isNum(m?.throughput) ? `${fmt(m.throughput, 0)}/h` : dash} sub="vehicles arrived" />
    </div>
  );
}

export function IncidentList({ incidents, zone }) {
  const actual = incidents?.actual || {};
  const known = incidents?.known || {};
  const edges = [...new Set([...Object.keys(actual), ...Object.keys(known)])];
  return (
    <section className="card stack" aria-labelledby="inc-h">
      <div className="card-head">
        <h2 id="inc-h">Incidents and closures</h2>
        <span className={`chip${edges.length ? ' bad' : ''}`}>{edges.length ? `${edges.length} active` : 'none'}</span>
      </div>
      {!incidents && <p className="muted small" style={{ margin: 0 }}>No incident data in this run.</p>}
      {incidents && !edges.length && <p className="muted small" style={{ margin: 0 }}>All roads are open.</p>}
      {edges.length > 0 && (
        <ul className="plain-list">
          {edges.map((e) => {
            const kind = actual[e] || known[e];
            const inZone = zone && /\.(z|cf)$/.test(e);
            const state = inZone && !known[e] ? 'inside the accident zone, handled by the zone signals'
              : actual[e] && known[e] ? 'detected over VANET; traffic is being rerouted'
              : actual[e] ? (incidents.detection ? 'not yet detected by the roadside units' : 'in place')
                : 'reported by the roadside units';
            return <li key={e}><strong>{KIND_TEXT[kind] || kind}</strong> on road {e} <span className="muted">· {state}</span></li>;
          })}
        </ul>
      )}
      {zone?.mode === 'alternating' && <p className="small" style={{ margin: 0 }}>Alternating one-way traffic is active on the shared lane (see Accident zone).</p>}
    </section>
  );
}

export function AmbulanceList({ ev, routes, onFollow }) {
  if (!ev?.length) return null;
  return (
    <section className="card stack" aria-labelledby="amb-h">
      <div className="card-head"><h2 id="amb-h">Ambulances</h2><span className="chip bad">{ev.length} active</span></div>
      <ul className="plain-list">
        {ev.map((e) => {
          const left = routes?.[e.id];
          return (
            <li key={e.id}>
              <strong>{e.id}</strong> → junction {e.tls}: ETA {fmt(e.eta)} s, {fmt(e.dist, 0)} m, {fmt((e.speed ?? 0) * 3.6, 0)} km/h
              {left?.length ? <span className="muted"> · {left.length} road{left.length === 1 ? '' : 's'} left to {left[left.length - 1]}</span> : null}
              {' '}<button type="button" className="link-btn" onClick={() => onFollow(e.id)}>Follow</button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
