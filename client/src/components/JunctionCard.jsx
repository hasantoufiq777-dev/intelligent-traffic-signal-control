import { fmt } from '../lib/format.js';

const DIRS = ['N', 'S', 'E', 'W'];

export default function JunctionCard({ id, data, ev, maxQueue = 10 }) {
  if (!data) return null;
  const evHere = ev?.find((e) => e.tls === id);
  const scale = Math.max(maxQueue, 1);
  return (
    <section className={`card jcard${data.phase === 'EV_GREEN' ? ' ev' : ''}`} aria-label={`Junction ${id}`}>
      <div className="card-head">
        <h2>{id}</h2>
        <span className="chip">{data.phase}</span>
      </div>
      <div className="qbars">
        {DIRS.map((d) => {
          const q = data.queues?.[d] ?? 0;
          const est = data.queues_est?.[d];
          const s = data.signal?.[d] || 'r';
          return [
            <span key={`${d}-s`} className={`sig sig-${s}`} title={`${d}: ${s}`}>{d}</span>,
            <span key={`${d}-b`} className="qtrack" aria-label={`${d} queue ${q}, estimated ${est ?? 'n/a'}`}>
              <span className="qbar" style={{ width: `${Math.min(100, (q / scale) * 100)}%` }} />
              {est != null && <span className="qbar est" style={{ width: `${Math.min(100, (est / scale) * 100)}%` }} />}
            </span>,
            <span key={`${d}-n`} className="num small">{q}{est != null && est !== q ? `/${est}` : ''}</span>,
          ];
        })}
      </div>
      <div className="small muted" style={{ marginTop: 8 }}>
        <span style={{ color: 'var(--series-1)' }}>■</span> true queue · <span style={{ color: 'var(--series-2)' }}>■</span> VANET estimate (number after /)
      </div>
      {evHere && (
        <div className="notice" style={{ marginTop: 8 }}>
          🚑 <strong>{evHere.id}</strong> ETA {fmt(evHere.eta)} s · {fmt(evHere.dist, 0)} m · alert age {fmt(evHere.alert_age, 1)} s
        </div>
      )}
    </section>
  );
}
