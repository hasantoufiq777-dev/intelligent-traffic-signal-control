import { memo, useMemo } from 'react';
import { FLAG, has, KIND_LABEL, vehicleKind } from '../lib/vehicles.js';

const PAD = 8;
// The map exaggerates road width so roads are easy to see. Each axis is mapped on its own: within a
// road's real half-width of a road line (junction x for vertical roads, junction y for horizontal ones)
// distances are stretched ROAD_SCALE x, and the space between roads absorbs the difference. Lanes,
// junction interiors, turning vehicles, stop lines and signal heads therefore all stay consistent.
const ROAD_SCALE = 3;
const LANE_W = 3.2;
const ARM_M = 120; // how much of the entry roads beyond the outer junctions is shown (m)
const CAR_W = 4.4; // drawn larger than real (2 m x 5 m) so cars are visible at map scale
const CAR_L = 9.5;

const KIND_STYLE = {
  plain: { fill: 'var(--car)' },
  equipped: { fill: 'var(--series-1)' },
  nav: { fill: 'var(--series-1)' },
  eco: { fill: 'var(--series-3)', stroke: 'var(--surface-1)' },
  emergency: { fill: 'var(--sig-red)', stroke: '#ffffff' },
};
const SIG_FILL = { G: 'var(--sig-green)', y: 'var(--sig-yellow)', r: 'var(--sig-red)' };

/** Direction an edge arrives from at its end junction (SUMO coordinates: y grows north). */
function approachOf(shape) {
  const [x1, y1] = shape[shape.length - 2];
  const [x2, y2] = shape[shape.length - 1];
  if (Math.abs(y2 - y1) >= Math.abs(x2 - x1)) return y2 > y1 ? 'S' : 'N';
  return x2 > x1 ? 'W' : 'E';
}

function TrafficMapInner({ network, vehicles, tlsState, followId, onSelect }) {
  const geo = useMemo(() => {
    if (!network) return null;
    const node = Object.fromEntries(network.junctions.map((j) => [j.id, j]));

    // One road per node pair (both directions together), drawn along the junction-to-junction line
    const segMap = new Map();
    for (const e of network.edges) {
      const a = node[e.from];
      const b = node[e.to];
      if (!a || !b) continue;
      const key = [e.from, e.to].sort().join('|');
      const seg = segMap.get(key) || { key, ax: a.x, ay: a.y, bx: b.x, by: b.y, lanes: 0 };
      seg.lanes += Math.max(1, e.lanes);
      segMap.set(key, seg);
    }
    const segs = [...segMap.values()];
    const lanesBoth = Math.max(...segs.map((s) => s.lanes), 2);
    const halfReal = (lanesBoth / 2) * LANE_W; // real half-width of a road (one direction)
    const roadW = lanesBoth * LANE_W * ROAD_SCALE; // drawn width

    const tlsJ = network.junctions.filter((j) => j.tls);
    const area = tlsJ.length ? tlsJ : network.junctions;

    /** Piecewise-linear map for one axis: stretch +/- halfReal around each road line by ROAD_SCALE. */
    const axisMap = (lines) => {
      const pts = [];
      for (const c of [...new Set(lines)].sort((a, b) => a - b)) {
        pts.push([c - halfReal, c - halfReal * ROAD_SCALE], [c + halfReal, c + halfReal * ROAD_SCALE]);
      }
      return (v) => {
        if (!pts.length) return v;
        if (v <= pts[0][0]) return v + (pts[0][1] - pts[0][0]);
        for (let i = 1; i < pts.length; i += 1) {
          const [a0, b0] = pts[i - 1];
          const [a1, b1] = pts[i];
          if (v <= a1) return b0 + ((v - a0) / (a1 - a0 || 1)) * (b1 - b0);
        }
        const [aN, bN] = pts[pts.length - 1];
        return v + (bN - aN);
      };
    };
    const mapX = axisMap(area.map((j) => j.x));
    const mapY = axisMap(area.map((j) => j.y));
    const widen = (x, y) => [mapX(x), mapY(y)];

    // Crop to the signalised area plus part of the entry roads (in drawn coordinates)
    const minX = mapX(Math.min(...area.map((j) => j.x)) - ARM_M);
    const maxX = mapX(Math.max(...area.map((j) => j.x)) + ARM_M);
    const minY = mapY(Math.min(...area.map((j) => j.y)) - ARM_M);
    const maxY = mapY(Math.max(...area.map((j) => j.y)) + ARM_M);
    const tx = (x) => x - minX + PAD; // x already mapped
    const ty = (y) => maxY - y + PAD; // y already mapped; flip: north up

    // Junction interior: from the centre to the real stop lines (ends of incoming edge shapes)
    let jHalfReal = halfReal;
    for (const e of network.edges) {
      const end = node[e.to];
      if (!end?.tls || !e.shape.length) continue;
      const [x2, y2] = e.shape[e.shape.length - 1];
      jHalfReal = Math.max(jHalfReal, Math.max(Math.abs(x2 - end.x), Math.abs(y2 - end.y)));
    }
    const ref = area[0];
    const jHalf = ref ? mapX(ref.x + jHalfReal) - mapX(ref.x) : roadW / 2;

    // Signal heads: on the incoming lanes just before the real stop line (end of the edge shape)
    const heads = [];
    for (const e of network.edges) {
      const end = node[e.to];
      if (!end?.tls || e.shape.length < 2) continue;
      const [x1, y1] = e.shape[e.shape.length - 2];
      const [x2, y2] = e.shape[e.shape.length - 1];
      const len = Math.hypot(x2 - x1, y2 - y1) || 1;
      const [px, py] = widen(x2 - ((x2 - x1) / len) * 3, y2 - ((y2 - y1) / len) * 3);
      heads.push({ id: e.id, tls: e.to, approach: approachOf(e.shape), x: tx(px), y: ty(py) });
    }
    return {
      width: maxX - minX + 2 * PAD,
      height: maxY - minY + 2 * PAD,
      tx,
      ty,
      widen,
      roadW,
      jHalf,
      roads: segs.map((s) => ({
        ...s, x1: tx(mapX(s.ax)), y1: ty(mapY(s.ay)), x2: tx(mapX(s.bx)), y2: ty(mapY(s.by)),
        w: s.lanes * LANE_W * ROAD_SCALE,
      })),
      junctions: tlsJ.map((j) => ({ ...j, X: tx(mapX(j.x)), Y: ty(mapY(j.y)) })),
      heads,
    };
  }, [network]);

  if (!geo) {
    return <div className="notice">Waiting for the road map from the simulation…</div>;
  }

  const rows = vehicles?.rows || [];
  const followed = rows.find((r) => r[0] === followId);

  return (
    <div className="map-wrap">
      <svg
        className="map-svg"
        viewBox={`0 0 ${geo.width} ${geo.height}`}
        role="img"
        aria-label={`Traffic map with ${rows.length} vehicles. Select a car to follow it.`}
      >
        <g>
          {geo.roads.map((r) => (
            <line key={`${r.key}-edge`} x1={r.x1} y1={r.y1} x2={r.x2} y2={r.y2} stroke="var(--road-edge)"
              strokeWidth={r.w + 2} strokeLinecap="butt" />
          ))}
          {geo.roads.map((r) => (
            <line key={r.key} x1={r.x1} y1={r.y1} x2={r.x2} y2={r.y2} stroke="var(--road)" strokeWidth={r.w}
              strokeLinecap="butt" />
          ))}
          {/* centre line between the two directions */}
          {geo.roads.map((r) => (
            <line key={`${r.key}-mid`} x1={r.x1} y1={r.y1} x2={r.x2} y2={r.y2} stroke="var(--surface-1)"
              strokeOpacity={0.7} strokeWidth={0.9} strokeDasharray="6 5" />
          ))}
        </g>
        <g>
          {geo.junctions.map((j) => (
            <g key={j.id}>
              <rect x={j.X - geo.jHalf} y={j.Y - geo.jHalf} width={geo.jHalf * 2} height={geo.jHalf * 2}
                fill="var(--junction)" />
              <text x={j.X + geo.jHalf + 4} y={j.Y - geo.jHalf - 4} fontSize={13} fontWeight={600}
                fill="var(--text-secondary)">{j.id}</text>
            </g>
          ))}
        </g>
        <g>
          {geo.heads.map((h) => {
            const s = tlsState?.[h.tls]?.signal?.[h.approach];
            return (
              <g key={h.id}>
                <circle cx={h.x} cy={h.y} r={4.5} fill={SIG_FILL[s] || 'var(--surface-2)'} stroke="var(--surface-1)" strokeWidth={1}>
                  <title>{`${h.tls} ${h.approach}: ${s === 'G' ? 'green' : s === 'y' ? 'yellow' : s === 'r' ? 'red' : 'unknown'}`}</title>
                </circle>
              </g>
            );
          })}
        </g>
        <g>
          {rows.map(([id, x, y, angle, flags]) => {
            const kind = vehicleKind(flags);
            const st = KIND_STYLE[kind];
            const big = kind === 'emergency' ? 1.35 : 1;
            const [wx, wy] = geo.widen(x, y);
            return (
              <g
                key={id}
                className="car-g"
                style={{ transform: `translate(${geo.tx(wx)}px, ${geo.ty(wy)}px) rotate(${angle}deg)` }}
                onClick={() => onSelect?.(id)}
              >
                <title>{`${id} – ${KIND_LABEL[kind]}${has(flags, FLAG.ADVISED) ? ' (slowing for green)' : ''}`}</title>
                {/* invisible larger hit area */}
                <circle r={8} fill="transparent" />
                <rect
                  x={(-CAR_W * big) / 2}
                  y={(-CAR_L * big) / 2}
                  width={CAR_W * big}
                  height={CAR_L * big}
                  rx={1.2}
                  fill={st.fill}
                  stroke={st.stroke || 'none'}
                  strokeWidth={st.stroke ? 0.9 : 0}
                />
                {kind === 'emergency' && (
                  <path d="M0 -2.2 V2.2 M-2.2 0 H2.2" stroke="#ffffff" strokeWidth={1.1} />
                )}
                {has(flags, FLAG.ADVISED) && <circle cx={0} cy={-CAR_L / 2 - 2} r={1.3} fill="var(--sig-yellow)" />}
              </g>
            );
          })}
        </g>
        {followed && (
          <g className="car-g" style={{ transform: `translate(${geo.tx(geo.widen(followed[1], followed[2])[0])}px, ${geo.ty(geo.widen(followed[1], followed[2])[1])}px)` }} pointerEvents="none">
            <circle r={12} fill="none" stroke="var(--text-primary)" strokeWidth={1.8} />
            <text y={-16} textAnchor="middle" fontSize={12} fontWeight={700} fill="var(--text-primary)"
              stroke="var(--surface-1)" strokeWidth={3} paintOrder="stroke">You</text>
          </g>
        )}
      </svg>
      <div className="map-legend" aria-label="Map legend">
        <span><svg width="10" height="16"><rect x="2" y="1" width="6" height="14" rx="1.5" fill="var(--car)" /></svg>Car without radio</span>
        <span><svg width="10" height="16"><rect x="2" y="1" width="6" height="14" rx="1.5" fill="var(--series-1)" /></svg>Connected car</span>
        <span><svg width="10" height="16"><rect x="2" y="1" width="6" height="14" rx="1.5" fill="var(--series-3)" stroke="var(--surface-1)" /></svg>Eco car</span>
        <span><svg width="12" height="18"><rect x="1" y="1" width="10" height="16" rx="2" fill="var(--sig-red)" /><path d="M6 6 V12 M3 9 H9" stroke="#fff" strokeWidth="1.5" /></svg>Ambulance</span>
        <span><svg width="10" height="10"><circle cx="5" cy="5" r="3" fill="var(--sig-yellow)" /></svg>Slowing for green</span>
        <span><svg width="16" height="16"><circle cx="8" cy="8" r="6.5" fill="none" stroke="var(--text-primary)" strokeWidth="1.5" /></svg>Your car</span>
      </div>
    </div>
  );
}

export default memo(TrafficMapInner);
