import { useMemo } from 'react';
import { fmt, isNum } from '../lib/format.js';
import { TimeChart } from './LiveCharts.jsx';

export function VanetPanel({ comm, states }) {
  const pdr = comm?.pdr;
  const data = useMemo(() => states.slice(-120).map((s) => ({ t: s.t, pdr: isNum(s.comm?.pdr) ? s.comm.pdr * 100 : null })), [states]);
  const unused = !comm || !comm.sent; // oracle-sensed controllers send no VANET messages
  const pct = !unused && isNum(pdr) ? Math.round(pdr * 100) : null;
  return (
    <div className="stack">
      <section className="card stack" aria-labelledby="vanet-h">
        <div className="card-head">
          <h2 id="vanet-h">VANET</h2>
          <span className="small muted">{unused ? 'not used in this run (signals read sensors directly)' : 'emulated V2V / V2I channel'}</span>
        </div>
        <div>
          <div className="small muted">Packet delivery ratio</div>
          <div className="row" style={{ alignItems: 'baseline' }}>
            <span style={{ fontSize: 32, fontWeight: 700 }} className="num">{pct != null ? `${pct}%` : '–'}</span>
          </div>
          <div style={{ height: 8, background: 'var(--surface-2)', borderRadius: 4 }} role="meter" aria-valuemin={0}
            aria-valuemax={100} aria-valuenow={pct ?? 0} aria-label="Packet delivery ratio">
            <div style={{ width: `${pct ?? 0}%`, height: 8, background: 'var(--series-1)', borderRadius: 4 }} />
          </div>
        </div>
        <div className="tiles">
          <div className="tile"><div className="k">Latency</div><div className="v num">{fmt(comm?.latency_ms, 0)} ms</div></div>
          <div className="tile"><div className="k">Equipped share</div><div className="v num">{isNum(comm?.penetration) ? `${Math.round(comm.penetration * 100)}%` : '–'}</div></div>
          <div className="tile"><div className="k">Messages sent</div><div className="v num">{comm?.sent ?? '–'}</div></div>
          <div className="tile"><div className="k">Lost</div><div className="v num">{comm?.lost ?? '–'}</div></div>
        </div>
      </section>
      <TimeChart title="Delivery ratio over time" unit="%" data={data} series={[{ key: 'pdr', name: 'PDR' }]} height={140} domain={[0, 100]} />
    </div>
  );
}

export function NavPanel({ nav, states }) {
  const data = useMemo(() => states.slice(-120).map((s) => ({
    t: s.t,
    nav: s.nav?.avg_tt_nav || null,
    non: s.nav?.avg_tt_non || null,
    cv: s.nav?.edge_load_cv ?? null,
  })), [states]);
  const off = !nav || nav.mode === 'none';
  return (
    <div className="stack">
      <section className="card stack" aria-labelledby="nav-h">
        <div className="card-head"><h2 id="nav-h">Navigation</h2><span className="chip">{off ? 'off' : nav.mode.toUpperCase()}</span></div>
        <div className="tiles">
          <div className="tile"><div className="k">Navigating cars</div><div className="v num">{off ? 0 : nav?.navigating ?? 0}</div></div>
          <div className="tile"><div className="k">Trip time, navigating</div><div className="v num">{off ? '–' : `${fmt(nav.avg_tt_nav, 0)} s`}</div></div>
          <div className="tile"><div className="k">Trip time, others</div><div className="v num">{off ? '–' : `${fmt(nav.avg_tt_non, 0)} s`}</div></div>
          <div className="tile"><div className="k">Busiest road</div><div className="v num">{nav?.max_edge_load ?? '–'} cars</div></div>
        </div>
      </section>
      {!off && (
        <TimeChart title="Average trip time" unit="s" data={data}
          series={[{ key: 'nav', name: 'Navigating' }, { key: 'non', name: 'Not navigating' }]} height={150} />
      )}
      <TimeChart title="Edge-load spread (CV, lower = more even)" data={data} series={[{ key: 'cv', name: 'Edge-load CV' }]} height={130} />
    </div>
  );
}
