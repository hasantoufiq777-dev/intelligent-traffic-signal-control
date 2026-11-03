import { useEffect, useMemo, useState } from 'react';
import {
  CartesianGrid, ErrorBar, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { api } from '../api.js';
import ChartTooltip from '../components/ChartTooltip.jsx';
import { SERIES } from '../components/LiveCharts.jsx';
import { fmt, fmtP, fmtPct } from '../lib/format.js';

const TABS = [
  { id: 'signals', title: 'Signals' },
  { id: 'ev', title: 'Emergency' },
  { id: 'nav', title: 'Navigation' },
  { id: 'eco', title: 'Eco-driving' },
];

const METRIC_LABEL = {
  avg_waiting_time: 'Waiting time (s)', avg_travel_time: 'Travel time (s)', avg_time_loss: 'Time loss (s)',
  throughput_veh_per_hr: 'Throughput (veh/h)', max_queue: 'Max queue', queue_estimate_mae: 'Queue estimate error',
  pdr: 'PDR', ev_travel_time: 'EV travel time (s)', missed_preemptions: 'Missed preemptions',
  ev_alert_delivery_time: 'Alert delivery (s)', avg_hops: 'Avg hops', nav_avg_travel_time: 'Navigating trip (s)',
  nonnav_avg_travel_time: 'Other trips (s)', edge_load_cv: 'Edge-load CV', max_edge_load: 'Max edge load',
  route_search_ms: 'Search time (ms)', route_search_expansions: 'Expansions', reroute_count: 'Reroutes',
  incident_detection_delay: 'Detection delay (s)', incident_rerouted_vehicles: 'Rerouted vehicles',
  co2_g_per_km: 'CO₂ (g/km)', fuel_l_per_100km: 'Fuel (L/100 km)', stops_per_vehicle: 'Stops per vehicle',
  co2_g_per_km_eco: 'CO₂ eco cars', co2_g_per_km_non: 'CO₂ other cars', stops_per_vehicle_eco: 'Stops eco cars',
  stops_per_vehicle_non: 'Stops other cars', eco_advice_share_slow: 'Slow-advice share', spat_pdr: 'SPaT delivery',
};

const KEY_METRICS = {
  signals: ['avg_waiting_time', 'avg_travel_time', 'throughput_veh_per_hr', 'queue_estimate_mae'],
  ev: ['ev_travel_time', 'avg_waiting_time', 'missed_preemptions', 'pdr'],
  nav: ['avg_travel_time', 'nav_avg_travel_time', 'nonnav_avg_travel_time', 'edge_load_cv', 'route_search_ms'],
  eco: ['co2_g_per_km', 'fuel_l_per_100km', 'stops_per_vehicle', 'avg_travel_time'],
};

const param = (label, name) => {
  const m = label.match(new RegExp(`${name}([0-9.]+)`));
  return m ? parseFloat(m[1].replace(/\.$/, '')) : null;
};
const half = (e) => (e?.ci95 ? Math.max(0, (e.ci95[1] - e.ci95[0]) / 2) : 0);

/** Line chart with 95% CI error bars; series: [{name, points: [{x, y, err}]}]; refs: [{name, y}]. */
function SeriesChart({ title, xLabel, yLabel, series, refs = [], xFormat = (v) => v }) {
  const data = useMemo(() => {
    const byX = new Map();
    series.forEach((s, i) => s.points.forEach((p) => {
      const row = byX.get(p.x) || { x: p.x };
      row[`s${i}`] = p.y;
      row[`e${i}`] = p.err;
      byX.set(p.x, row);
    }));
    return [...byX.values()].sort((a, b) => a.x - b.x);
  }, [series]);
  if (!data.length) return null;
  return (
    <section className="card" aria-label={title}>
      <div className="card-head"><h2>{title}</h2><span className="small muted">error bars: 95% CI</span></div>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 18, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="x" type="number" domain={['dataMin', 'dataMax']} stroke="var(--axis)" tickLine={false}
            tickFormatter={xFormat} label={{ value: xLabel, position: 'insideBottom', offset: -8, fill: 'var(--text-muted)', fontSize: 12 }} />
          <YAxis stroke="var(--axis)" tickLine={false} width={52} domain={[0, 'auto']} allowDataOverflow
            label={{ value: yLabel, angle: -90, position: 'insideLeft', fill: 'var(--text-muted)', fontSize: 12, dy: 50 }} />
          <Tooltip content={<ChartTooltip labelFormatter={(l) => `${xLabel}: ${xFormat(l)}`} valueFormatter={(v) => fmt(v, 1)} />} />
          <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, paddingTop: 12 }} />
          {refs.map((r, i) => (
            <ReferenceLine key={r.name} y={r.y} stroke="var(--text-muted)" strokeDasharray={i ? '2 3' : '6 4'}
              label={{ value: r.name, position: i ? 'insideTopLeft' : 'insideTopRight', fill: 'var(--text-secondary)', fontSize: 11 }} />
          ))}
          {series.map((s, i) => (
            <Line key={s.name} dataKey={`s${i}`} name={s.name} stroke={SERIES[i % SERIES.length]} strokeWidth={2}
              dot={{ r: 4 }} isAnimationActive={false} connectNulls>
              <ErrorBar dataKey={`e${i}`} width={4} strokeWidth={1.2} stroke={SERIES[i % SERIES.length]} direction="y" />
            </Line>
          ))}
        </LineChart>
      </ResponsiveContainer>
    </section>
  );
}

function ResultsTable({ suite, scen, cfgs, meta }) {
  const metrics = KEY_METRICS[suite].filter((m) => Object.values(cfgs).some((c) => c[m]));
  return (
    <section className="card" aria-label={`${scen} results table`}>
      <div className="card-head">
        <h2>Scenario: {scen}</h2>
        <span className="small muted">mean ± std (improvement vs baseline, paired p-value)</span>
      </div>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr><th>Configuration</th>{metrics.map((m) => <th key={m}>{METRIC_LABEL[m] || m}</th>)}</tr>
          </thead>
          <tbody>
            {Object.entries(cfgs).map(([label, c]) => (
              <tr key={label}>
                <td title={meta?.[label]?.description}>{label}{meta?.[label]?.baseline === label ? ' (baseline)' : ''}</td>
                {metrics.map((m) => {
                  const e = c[m];
                  if (!e) return <td key={m}>–</td>;
                  const imp = e.improvement_pct;
                  return (
                    <td key={m}>
                      {fmt(e.mean, 2)} ± {fmt(e.std, 2)}
                      {imp != null && (
                        <span className={imp > 0 ? 'good' : imp < 0 ? 'bad' : ''}> ({fmtPct(imp)}, p={fmtP(e.p_value)})</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function pts(cfgs, prefix, metric, name, filter = () => true) {
  return Object.entries(cfgs)
    .filter(([l]) => l.startsWith(prefix) && filter(l))
    .map(([l, c]) => ({ x: param(l, name), y: c[metric]?.mean, err: half(c[metric]) }))
    .filter((p) => p.x != null && p.y != null);
}
const ref = (cfgs, label, metric, name) => (cfgs[label]?.[metric] ? [{ name, y: cfgs[label][metric].mean }] : []);

function SuiteCharts({ suite, scen, cfgs }) {
  if (suite === 'signals') {
    return (
      <SeriesChart title={`Waiting time vs OBU penetration · ${scen}`} xLabel="Penetration" yLabel="Waiting time (s)"
        xFormat={(v) => `${Math.round(v * 100)}%`}
        series={[{ name: 'adaptive_vanet', points: pts(cfgs, 'adaptive_vanet@', 'avg_waiting_time', 'pen') }]}
        refs={[...ref(cfgs, 'adaptive', 'avg_waiting_time', 'adaptive (oracle)'), ...ref(cfgs, 'fixed', 'avg_waiting_time', 'fixed')]} />
    );
  }
  if (suite === 'ev') {
    return (
      <SeriesChart title="EV travel time vs channel loss" xLabel="Packet loss" yLabel="EV travel time (s)"
        xFormat={(v) => `${Math.round(v * 100)}%`}
        series={['on', 'off'].map((r) => ({ name: `VANET preemption, relay ${r}`, points: pts(cfgs, 'adaptive_ev_vanet@', 'ev_travel_time', 'loss', (l) => l.endsWith(`relay_${r}`)) }))}
        refs={[...ref(cfgs, 'adaptive_ev', 'ev_travel_time', 'oracle preemption'), ...ref(cfgs, 'adaptive', 'ev_travel_time', 'no preemption')]} />
    );
  }
  if (suite === 'nav') {
    const pens = [...new Set(Object.keys(cfgs).filter((l) => l.startsWith('none@')).map((l) => param(l, 'pen')))].sort((a, b) => b - a);
    const pen = pens[0];
    const atPen = (l) => param(l, '_pen') === pen;
    const modes = ['astar', 'aco', 'aco_tuned'];
    return (
      <div className="overview-grid">
        <SeriesChart title={`Travel time of navigating cars · ${scen} (penetration ${pen})`} xLabel="Navigating fraction" yLabel="Travel time (s)"
          series={modes.map((m) => ({ name: m, points: pts(cfgs, `${m}@`, 'nav_avg_travel_time', 'frac', atPen) })).filter((s) => s.points.length)}
          refs={ref(cfgs, `none@pen${pen}`, 'avg_travel_time', 'no navigation')} />
        <SeriesChart title="Edge-load spread (lower = more even)" xLabel="Navigating fraction" yLabel="Edge-load CV"
          series={modes.map((m) => ({ name: m, points: pts(cfgs, `${m}@`, 'edge_load_cv', 'frac', atPen) })).filter((s) => s.points.length)}
          refs={ref(cfgs, `none@pen${pen}`, 'edge_load_cv', 'no navigation')} />
      </div>
    );
  }
  const ctrls = [...new Set(Object.keys(cfgs).map((l) => l.split('@')[0]))];
  return (
    <div className="overview-grid">
      <SeriesChart title={`CO₂ vs eco-car share · ${scen}`} xLabel="Eco cars" yLabel="CO₂ (g/km)" xFormat={(v) => `${Math.round(v * 100)}%`}
        series={ctrls.map((c) => ({ name: c, points: pts(cfgs, `${c}@`, 'co2_g_per_km', 'eco') }))} />
      <SeriesChart title="Stops per vehicle vs eco-car share" xLabel="Eco cars" yLabel="Stops per vehicle" xFormat={(v) => `${Math.round(v * 100)}%`}
        series={ctrls.map((c) => ({ name: c, points: pts(cfgs, `${c}@`, 'stops_per_vehicle', 'eco') }))} />
    </div>
  );
}

export default function Results() {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState('signals');

  useEffect(() => {
    api.summary().then(setSummary).catch((e) => setError(e));
  }, []);

  if (error) {
    return (
      <div className="notice">
        <strong>No experiment results yet.</strong>
        <div className="small" style={{ marginTop: 4 }}>
          {error.status === 404
            ? <>Run the evaluation, then reload: <code>python sim/analytics/evaluate.py --suite all</code></>
            : error.message}
        </div>
      </div>
    );
  }
  if (!summary) return <div className="notice">Loading results…</div>;

  const suite = summary.suites?.[tab] || {};
  return (
    <div className="stack">
      <div className="row">
        <h1 style={{ fontSize: 20 }}>Experiment results</h1>
        <span className="small muted">
          Generated {new Date(summary.generated_at).toLocaleString()} · seeds {summary.meta?.seeds?.join(', ')}
          {summary.meta?.quick ? ' · quick run' : ''}
        </span>
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={tab === t.id ? 'active' : ''}
            onClick={() => setTab(t.id)}>{t.title}</button>
        ))}
      </div>
      {Object.keys(suite).length === 0 && <div className="notice">This suite has not been run yet.</div>}
      {Object.entries(suite).map(([scen, cfgs]) => (
        <div className="stack" key={scen}>
          <SuiteCharts suite={tab} scen={scen} cfgs={cfgs} />
          <ResultsTable suite={tab} scen={scen} cfgs={cfgs} meta={summary.meta?.configs?.[tab]} />
        </div>
      ))}
    </div>
  );
}
