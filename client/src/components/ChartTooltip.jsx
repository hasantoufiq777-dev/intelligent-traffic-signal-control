// Tooltip for Recharts: value first (strong), series name second, keyed by a short line in the series colour.
export default function ChartTooltip({ active, payload, label, labelFormatter, valueFormatter }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="small muted">{labelFormatter ? labelFormatter(label) : label}</div>
      {payload.map((p) => (
        <div className="tt-row" key={p.dataKey}>
          <span className="tt-key" style={{ background: p.color }} />
          <span className="tt-val">{valueFormatter ? valueFormatter(p.value, p) : p.value}</span>
          <span className="tt-name">{p.name}</span>
        </div>
      ))}
    </div>
  );
}
