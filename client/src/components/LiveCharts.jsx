import { memo, useMemo } from 'react';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import ChartTooltip from './ChartTooltip.jsx';

export const SERIES = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)', 'var(--series-5)'];
const axisProps = { stroke: 'var(--axis)', tickLine: false };

/** Small line chart over the last 120 s; series = [{key, name}]. One y-axis only. */
export const TimeChart = memo(function TimeChart({ title, data, series, unit, height = 180, domain }) {
  return (
    <section className="card" aria-label={title}>
      <div className="card-head"><h2>{title}</h2>{unit && <span className="small muted">{unit}</span>}</div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="t" {...axisProps} tickFormatter={(v) => `${v}s`} minTickGap={24} />
          <YAxis {...axisProps} width={44} domain={domain} allowDecimals />
          <Tooltip content={<ChartTooltip labelFormatter={(l) => `t = ${l} s`} />} cursor={{ stroke: 'var(--axis)' }} />
          {series.length > 1 && <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />}
          {series.map((s, i) => (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={SERIES[i % SERIES.length]}
              strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </section>
  );
});

export default function LiveCharts({ states }) {
  const tlsIds = useMemo(() => Object.keys(states.at(-1)?.tls || {}).sort(), [states]);
  const data = useMemo(() => states.slice(-120).map((s) => {
    const row = { t: s.t, wait: s.metrics?.avg_wait, thr: s.metrics?.throughput, veh: s.metrics?.n_vehicles };
    for (const id of tlsIds) {
      const q = s.tls?.[id]?.queues || {};
      row[`q_${id}`] = (q.N || 0) + (q.S || 0) + (q.E || 0) + (q.W || 0);
    }
    return row;
  }), [states, tlsIds]);

  return (
    <div className="charts-grid">
      <TimeChart title="Queued vehicles per junction" unit="vehicles" data={data}
        series={tlsIds.map((id) => ({ key: `q_${id}`, name: id }))} />
      <TimeChart title="Average waiting time" unit="s" data={data} series={[{ key: 'wait', name: 'Waiting time' }]} />
      <TimeChart title="Throughput" unit="vehicles/h" data={data} series={[{ key: 'thr', name: 'Throughput' }]} />
    </div>
  );
}
