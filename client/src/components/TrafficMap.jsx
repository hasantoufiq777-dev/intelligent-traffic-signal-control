// Live traffic map. Everything drawn comes from the simulation's messages (road network, vehicle
// rows, signal states, road speeds, incidents, ambulance routes, accident zone); nothing is made up
// in the browser. Vehicles glide between the once-a-second positions with a CSS transition.
import { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { CATEGORY_LABEL, categoryOf, continuousAngle, FLAG, fieldIndex, has, KIND_LABEL, sizeOf, vehicleKind } from '../lib/vehicles.js';
import { CarBody, CarIcon } from './CarShape.jsx';
import Icon from './Icon.jsx';

const PAD = 8;
// The map exaggerates road width so roads are easy to see. Each axis is mapped on its own: within a
// road's real half-width of a road line (junction x for vertical roads, junction y for horizontal ones)
// distances are stretched ROAD_SCALE x, and the space between roads absorbs the difference. Lanes,
// junction interiors, turning vehicles, stop lines and signal heads therefore all stay consistent.
const ROAD_SCALE = 3;
const LANE_W = 3.2;
const ARM_M = 120; // how much of the entry roads beyond the outer junctions is shown (m)
// Cars are drawn a little larger than real so they are visible at map scale, but never longer than the
// space SUMO keeps for them: in a queue a follower's front is minGap (2.5 m) behind the leader's rear,
// so a drawn length of real length + 1.5 m leaves a 1 m gap. SUMO's position is the FRONT bumper, so the
// body is drawn from there backwards (drawing it centred made queued cars overlap the car ahead).
const CAR_W = 4.2;
const EXTRA_L = 1.5;
const WIDTH_SCALE = CAR_W / 1.8; // a 1.8 m wide car is drawn CAR_W wide; other vehicles in proportion
const CITY_PAD_M = 40; // city network: the whole network is shown, plus this margin
// City network: drawn at its real geometry (lane shapes and widths); vehicles a little larger than real
const CITY_VEHICLE_SCALE = 1.15;
const CITY_MAX_ZOOM = 10;
// Route explorer colours (route 1 = fastest is listed first by the simulation)
export const ROUTE_COLORS = ['#2563eb', '#d97706', '#7c3aed', '#0e9488'];
const MAX_ZOOM = 6;

/**
 * Congestion of one road direction: 'congested' or null. A low speed alone is not congestion
 * (cars waiting at a red light are slow too), so a road that ends at a signal also needs a queue.
 */
export function congestion(speed, limit, queue) {
  if (!(limit > 0) || speed == null) return null;
  const ratio = speed / limit;
  if (queue == null) return ratio < 0.2 ? 'congested' : null; // exit road: only a standstill counts
  return ratio < 0.35 && queue >= 6 ? 'congested' : null;
}

const ROAD_TYPE_TEXT = {
  grid: 'Street between the signalised junctions, 50 km/h',
  main: 'Main road: 3 lanes each way, 60 km/h',
  secondary: 'Secondary road: 2 lanes each way, 50 km/h',
  collector: 'Collector road: 2 narrow lanes each way, 40 km/h',
  local: 'Local sub-road: 1 narrow lane each way, 30 km/h, no trucks or buses, stop sign at the end',
};

const SIG_FILL = { G: 'var(--sig-green)', y: 'var(--sig-yellow)', r: 'var(--sig-red)' };
const SIG_WORD = { G: 'green', y: 'yellow', r: 'red' };
const APPROACH_WORD = { N: 'north', S: 'south', E: 'east', W: 'west' };

export const LAYERS = [
  { id: 'vehicles', label: 'Vehicles', on: true },
  { id: 'queues', label: 'Queue counts', on: true },
  { id: 'timers', label: 'Signal countdowns', on: true },
  { id: 'congestion', label: 'Congestion (road speed)', on: true },
  { id: 'incidents', label: 'Accidents and closures', on: true },
  { id: 'routes', label: 'Ambulance routes', on: true },
  { id: 'rsu', label: 'Roadside units (RSU)', on: true },
  { id: 'range', label: 'VANET radio range', on: false },
  { id: 'labels', label: 'Road names', on: false },
  { id: 'lengths', label: 'Road lengths', on: true },
];
const LAYER_KEY = 'itsc-map-layers';

function loadLayers() {
  const base = Object.fromEntries(LAYERS.map((l) => [l.id, l.on]));
  try {
    return { ...base, ...JSON.parse(localStorage.getItem(LAYER_KEY) || '{}') };
  } catch {
    return base;
  }
}

/** A SUMO polyline moved sideways by off metres (positive = to the right of travel). */
function offsetPts(points, off) {
  return points.map((p, i) => {
    const a = points[Math.max(0, i - 1)];
    const b = points[Math.min(points.length - 1, i + 1)];
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    return [p[0] + ((b[1] - a[1]) / L) * off, p[1] - ((b[0] - a[0]) / L) * off];
  });
}

/** Direction an edge arrives from at its end junction (SUMO coordinates: y grows north). */
function approachOf(shape) {
  const [x1, y1] = shape[shape.length - 2];
  const [x2, y2] = shape[shape.length - 1];
  if (Math.abs(y2 - y1) >= Math.abs(x2 - x1)) return y2 > y1 ? 'S' : 'N';
  return x2 > x1 ? 'W' : 'E';
}

function buildGeometry(network) {
  const node = Object.fromEntries(network.junctions.map((j) => [j.id, j]));
  const contraflow = network.zone?.contraflow_edge;

  // Real width of one direction: its lane widths (city network) or lanes x 3.2 m
  const realWidth = (e) => (e.lane_info?.length ? e.lane_info.reduce((t, l) => t + (l.w || LANE_W), 0)
    : Math.max(1, e.lanes) * LANE_W);
  // City network: typed roads (ring, residential, bus lanes) around the signalised grid
  const isCity = network.edges.some((e) => e.type && e.type !== 'grid');

  // One road per node pair (both directions together), drawn along the junction-to-junction line
  const segMap = new Map();
  for (const e of network.edges) {
    const a = node[e.from];
    const b = node[e.to];
    if (!a || !b || e.id === contraflow) continue; // the contraflow lane lies on an existing lane
    const key = [e.from, e.to].sort().join('|');
    const seg = segMap.get(key) || { key, ax: a.x, ay: a.y, bx: b.x, by: b.y, lanes: 0, realW: 0, type: e.type || 'grid' };
    seg.lanes += Math.max(1, e.lanes);
    seg.realW += realWidth(e);
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
  // The grid networks stretch the space around their straight road lines so wide roads fit; the city
  // network (irregular junctions, bends) is drawn at its real geometry instead
  const mapX = isCity ? (v) => v : axisMap(area.map((j) => j.x));
  const mapY = isCity ? (v) => v : axisMap(area.map((j) => j.y));
  const S = isCity ? 1 : ROAD_SCALE; // drawn width per real metre

  // Crop to the signalised area plus part of the entry roads (city: the whole ring), drawn coordinates
  const cropJ = isCity ? network.junctions : area;
  const arm = isCity ? CITY_PAD_M : ARM_M;
  const minX = mapX(Math.min(...cropJ.map((j) => j.x)) - arm);
  const maxX = mapX(Math.max(...cropJ.map((j) => j.x)) + arm);
  const minY = mapY(Math.min(...cropJ.map((j) => j.y)) - arm);
  const maxY = mapY(Math.max(...cropJ.map((j) => j.y)) + arm);
  const tx = (x) => x - minX + PAD; // x already mapped
  const ty = (y) => maxY - y + PAD; // y already mapped; flip: north up
  /** SUMO coordinates -> map coordinates. */
  const pt = (x, y) => [tx(mapX(x)), ty(mapY(y))];
  const path = (pts) => pts.map(([x, y], i) => {
    const [px, py] = pt(x, y);
    return `${i ? 'L' : 'M'}${px.toFixed(1)} ${py.toFixed(1)}`;
  }).join(' ');

  // Junction interior: from the centre to the real stop lines (ends of incoming edge shapes)
  let jHalfReal = halfReal;
  for (const e of network.edges) {
    const end = node[e.to];
    if (!end?.tls || !e.shape.length) continue;
    const [x2, y2] = e.shape[e.shape.length - 1];
    jHalfReal = Math.max(jHalfReal, Math.max(Math.abs(x2 - end.x), Math.abs(y2 - end.y)));
  }
  const ref = area[0];
  let jHalf = ref ? mapX(ref.x + jHalfReal) - mapX(ref.x) : roadW / 2;
  if (isCity) {
    // half the size of the signalised junctions' real areas
    const halves = tlsJ.filter((j) => j.shape?.length > 2).map((j) => {
      const xs = j.shape.map((q) => q[0]);
      const ys = j.shape.map((q) => q[1]);
      return Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) / 2;
    });
    if (halves.length) jHalf = halves.reduce((t, h) => t + h, 0) / halves.length;
  }

  // Per-direction road bands (congestion, closures, names, routes) and the signal heads
  const edges = {};
  const heads = [];
  for (const e of network.edges) {
    if (e.id === contraflow || e.shape.length < 2) continue;
    const [x1, y1] = e.shape[e.shape.length - 2];
    const [x2, y2] = e.shape[e.shape.length - 1];
    const len = Math.hypot(x2 - x1, y2 - y1) || 1;
    const ux = (x2 - x1) / len;
    const uy = (y2 - y1) / len;
    const [fx, fy] = e.shape[0];
    const horizontal = Math.abs(ux) >= Math.abs(uy);
    const end = node[e.to];
    const rw = realWidth(e);
    // City: the edge's shape is the road's centre line; this direction's carriageway lies right of it
    const band = isCity ? offsetPts(e.shape, rw / 2) : e.shape;
    const midPt = band[Math.floor((band.length - 1) / 2)];
    const midNext = band[Math.min(band.length - 1, Math.floor((band.length - 1) / 2) + 1)];
    edges[e.id] = {
      id: e.id, d: path(band), w: rw * S - (isCity ? 0.4 : 1.5), limit: e.speed ?? null, type: e.type || 'grid',
      mid: isCity ? pt((midPt[0] + midNext[0]) / 2, (midPt[1] + midNext[1]) / 2) : pt((fx + x2) / 2, (fy + y2) / 2),
      horizontal, from: e.from, to: e.to, length: e.length ?? null,
      tls: end?.tls ? e.to : null, approach: end?.tls ? approachOf(e.shape) : null,
    };
    if (!end?.tls) continue;
    // Right-hand side of the direction of travel = the outer edge of the road: room for the queue count
    const rx = uy;
    const ry = -ux;
    const side = isCity ? rw + 3 : halfReal + 4.5;
    const across = isCity ? rw / 2 : 0; // the head stands over the middle of the carriageway
    heads.push({
      id: e.id, tls: e.to, approach: approachOf(e.shape),
      pos: pt(x2 - ux * 3 + rx * across, y2 - uy * 3 + ry * across),
      badge: pt(x2 - ux * 14 + rx * side, y2 - uy * 14 + ry * side),
      timer: pt(x2 - ux * 3 + rx * side, y2 - uy * 3 + ry * side),
    });
  }

  // Lanes with restrictions (bus lane; cars, motorcycles and ambulances only), drawn on the road band:
  // lane i lies right of the centre line by the widths of the lanes inside it plus half its own width.
  const laneMarks = [];
  for (const e of network.edges) {
    const a = node[e.from];
    const b = node[e.to];
    if (!a || !b || !e.lane_info?.some((l) => l.allow)) continue;
    const [ax, ay] = pt(a.x, a.y);
    const [bx, by] = pt(b.x, b.y);
    const L = Math.hypot(bx - ax, by - ay) || 1;
    const dx = (bx - ax) / L;
    const dy = (by - ay) / L;
    const rx = -dy;
    const ry = dx; // right of travel on screen (y down)
    const trim = Math.min(L / 3, Math.max(jHalf, 10) + 4);
    e.lane_info.forEach((l, i) => {
      if (!l.allow) return;
      const inner = e.lane_info.slice(i + 1).reduce((t, x) => t + (x.w || LANE_W), 0);
      const off = (inner + (l.w || LANE_W) / 2) * ROAD_SCALE;
      const bus = l.allow.includes('bus') && !l.allow.includes('passenger');
      laneMarks.push({
        key: `${e.id}_${i}`, edge: e.id, bus, allow: l.allow,
        x1: ax + dx * trim + rx * off, y1: ay + dy * trim + ry * off,
        x2: bx - dx * trim + rx * off, y2: by - dy * trim + ry * off, w: (l.w || LANE_W) * ROAD_SCALE * 0.8,
      });
    });
  }

  // City: every lane along its SUMO shape at its real width, the markings between lanes (dashed) and
  // between the directions (solid), a direction arrow per lane, and the junctions' real areas
  const cityLanes = [];
  const cityMarks = [];
  const cityArrows = [];
  const junctionAreas = [];
  if (isCity) {
    for (const e of network.edges) {
      const lanes = e.lane_info ?? [];
      lanes.forEach((l, i) => {
        if (!l.shape?.length) return;
        const heavyBanned = l.allow && !l.allow.includes('truck');
        const bus = l.allow && l.allow.includes('bus') && !l.allow.includes('passenger');
        cityLanes.push({ key: `${e.id}_${i}`, d: path(l.shape), w: l.w || LANE_W, type: e.type,
          tint: bus ? 'bus' : heavyBanned && e.type !== 'local' ? 'noheavy' : null });
        const left = offsetPts(l.shape, -(l.w || LANE_W) / 2);
        cityMarks.push({ key: `${e.id}_${i}_m`, d: path(left), centre: i === lanes.length - 1 });
        const k = Math.floor((l.shape.length - 1) / 2);
        const [ax, ay] = pt(...l.shape[k]);
        const [bx, by] = pt(...l.shape[Math.min(l.shape.length - 1, k + 1)]);
        cityArrows.push({ key: `${e.id}_${i}_a`, x: (ax + bx) / 2, y: (ay + by) / 2,
          angle: (Math.atan2(by - ay, bx - ax) * 180) / Math.PI });
      });
    }
    for (const j of network.junctions) {
      if (j.shape?.length > 2) junctionAreas.push({ id: j.id, tls: j.tls, d: `${path(j.shape)} Z` });
    }
  }

  // Road lengths: one label per road (both directions), between its two carriageways
  const byRoad = {};
  for (const e of Object.values(edges)) {
    if (e.length == null) continue;
    const key = [e.from, e.to].sort().join('|');
    (byRoad[key] ??= []).push(e);
  }
  const degree = {};
  for (const key of Object.keys(byRoad)) for (const n of key.split('|')) degree[n] = (degree[n] ?? 0) + 1;
  const roadLengths = Object.entries(byRoad).map(([key, list]) => {
    const lens = [...new Set(list.map((e) => Math.round(e.length)))];
    let x = list.reduce((t, e) => t + e.mid[0], 0) / list.length;
    let y = list.reduce((t, e) => t + e.mid[1], 0) / list.length;
    // An exit road runs off the edge of the map: put its label near the junction it starts from
    const [n1, n2] = key.split('|');
    const exit = degree[n1] === 1 ? n1 : degree[n2] === 1 ? n2 : null;
    const inner = exit && node[exit === n1 ? n2 : n1];
    if (inner && node[exit]) {
      const [ix, iy] = pt(inner.x, inner.y);
      const [ox, oy] = pt(node[exit].x, node[exit].y);
      const L = Math.hypot(ox - ix, oy - iy) || 1;
      const d = Math.min(L / 2, jHalf + 60);
      x = ix + ((ox - ix) / L) * d;
      y = iy + ((oy - iy) / L) * d;
    }
    return {
      key,
      x,
      y,
      horizontal: list[0].horizontal,
      text: `${lens.join(' / ')} m`,
      title: list.map((e) => `${e.id}: ${e.length.toFixed(1)} m`).join(', '),
    };
  });

  const range = network.vanet?.active ? network.vanet.range_m : null;
  return {
    isCity,
    roadLengths,
    cityLanes,
    cityMarks,
    cityArrows,
    junctionAreas,
    laneMarks: isCity ? [] : laneMarks,
    width: maxX - minX + 2 * PAD,
    height: maxY - minY + 2 * PAD,
    pt,
    roadW,
    jHalf,
    edges,
    heads,
    roads: segs.map((s) => {
      const [x1, y1] = pt(s.ax, s.ay);
      const [x2, y2] = pt(s.bx, s.by);
      return { ...s, x1, y1, x2, y2, w: s.realW * ROAD_SCALE };
    }),
    junctions: tlsJ.map((j) => {
      const [X, Y] = pt(j.x, j.y);
      return { ...j, X, Y, R: range ? pt(j.x + range, j.y)[0] - X : null };
    }),
    vanet: network.vanet ?? null,
    zone: zoneGeometry(network.zone, pt),
  };
}

/** Zoom and pan state: scale k and offset (x, y) in map units, kept inside the map. */
function useView(geo) {
  const [view, setView] = useState({ k: 1, x: 0, y: 0 });
  const maxZoom = geo?.isCity ? CITY_MAX_ZOOM : MAX_ZOOM;
  const clamp = useCallback((v) => {
    if (!geo) return v;
    const k = Math.min(maxZoom, Math.max(1, v.k));
    return { k, x: Math.min(0, Math.max(geo.width * (1 - k), v.x)), y: Math.min(0, Math.max(geo.height * (1 - k), v.y)) };
  }, [geo]);
  const zoomAt = useCallback((factor, cx, cy) => setView((v) => {
    const k = Math.min(maxZoom, Math.max(1, v.k * factor));
    const f = k / v.k;
    return clamp({ k, x: cx - (cx - v.x) * f, y: cy - (cy - v.y) * f });
  }), [clamp, maxZoom]);
  const panBy = useCallback((dx, dy) => setView((v) => clamp({ ...v, x: v.x + dx, y: v.y + dy })), [clamp]);
  const reset = useCallback(() => setView({ k: 1, x: 0, y: 0 }), []);
  return { view, zoomAt, panBy, reset, maxZoom };
}

function TrafficMapInner({
  network, vehicles, tlsState, zoneState, edgeSpeeds, incidents, evRoutes, followId, onSelect,
  selectedJunction, onSelectJunction, stale, routeOverlay = null,
}) {
  const geo = useMemo(() => (network ? buildGeometry(network) : null), [network]);
  const hatchId = `no-heavy-${useId().replace(/[^a-zA-Z0-9]/g, '')}`; // unique per map on the page
  const svgRef = useRef(null);
  const drag = useRef(null);
  const [layers, setLayers] = useState(loadLayers);
  const [showLayers, setShowLayers] = useState(false);
  const { view, zoomAt, panBy, reset, maxZoom } = useView(geo);

  // Last drawn (continuous) heading per car, so rotations between frames always go the short way
  const headings = useRef(new Map());

  const toggleLayer = (id) => setLayers((l) => {
    const next = { ...l, [id]: !l[id] };
    try {
      localStorage.setItem(LAYER_KEY, JSON.stringify(next));
    } catch {
      /* per-viewer convenience only */
    }
    return next;
  });

  /** Client pixels -> map units. */
  const toMap = useCallback((clientX, clientY) => {
    const r = svgRef.current.getBoundingClientRect();
    const s = Math.max(geo.width / r.width, geo.height / r.height); // preserveAspectRatio "meet"
    return [(clientX - r.left - (r.width - geo.width / s) / 2) * s, (clientY - r.top - (r.height - geo.height / s) / 2) * s, s];
  }, [geo]);

  // Ctrl + wheel (and trackpad pinch) zooms about the pointer; a plain wheel still scrolls the page
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || !geo) return undefined;
    const onWheel = (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const [mx, my] = toMap(e.clientX, e.clientY);
      zoomAt(Math.exp(-e.deltaY * 0.0022), mx, my);
    };
    svg.addEventListener('wheel', onWheel, { passive: false });
    return () => svg.removeEventListener('wheel', onWheel);
  }, [geo, toMap, zoomAt]);

  if (!geo) {
    return <div className="notice" role="status">Waiting for the road map from the simulation…</div>;
  }

  const onPointerDown = (e) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    drag.current = { x: e.clientX, y: e.clientY, moved: false, id: e.pointerId };
  };
  const onPointerMove = (e) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) < 5) return;
    if (!d.moved) svgRef.current.setPointerCapture?.(e.pointerId);
    d.moved = true;
    d.x = e.clientX;
    d.y = e.clientY;
    const s = toMap(0, 0)[2];
    panBy(dx * s, dy * s);
  };
  const onPointerUp = () => {
    // A drag must not also count as a click on the car or junction under the pointer
    const d = drag.current;
    drag.current = d?.moved ? { moved: true, done: true } : null;
  };
  const clickUnlessDragged = (fn) => () => {
    if (drag.current?.done) {
      drag.current = null;
      return;
    }
    fn();
  };
  const onKeyDown = (e) => {
    const step = 40 / view.k;
    const cx = geo.width / 2;
    const cy = geo.height / 2;
    const act = { '+': () => zoomAt(1.3, cx, cy), '=': () => zoomAt(1.3, cx, cy), '-': () => zoomAt(1 / 1.3, cx, cy),
      0: reset, ArrowLeft: () => panBy(step, 0), ArrowRight: () => panBy(-step, 0), ArrowUp: () => panBy(0, step),
      ArrowDown: () => panBy(0, -step) }[e.key];
    if (act) {
      e.preventDefault();
      act();
    }
  };

  const rows = vehicles?.rows || [];
  const kindIdx = fieldIndex(vehicles, 'kind');
  const followed = rows.find((r) => r[0] === followId);
  const connected = rows.filter((r) => has(r[4], FLAG.EQUIPPED)).length;
  const prevHeadings = headings.current;
  const nextHeadings = new Map();
  for (const [id, , , angle] of rows) nextHeadings.set(id, continuousAngle(prevHeadings.get(id), angle));
  headings.current = nextHeadings; // cars that left the map are dropped

  const actual = incidents?.actual || {};
  const known = incidents?.known || {};
  // The accident zone draws its own crash, debris and shared lane; other incidents get the generic marker
  const incidentEdges = [...new Set([...Object.keys(actual), ...Object.keys(known)])]
    .filter((e) => geo.edges[e] && !(geo.zone && /\.(z|cf)$/.test(e)));
  const zoomed = view.k > 1.001;

  return (
    <div className="map-wrap">
      <div className="map-stage">
      <svg
        ref={svgRef}
        className={`map-svg${zoomed ? ' zoomed' : ''}`}
        viewBox={`0 0 ${geo.width} ${geo.height}`}
        role="application"
        aria-label={`Traffic map with ${rows.length} vehicles. Arrow keys move the map, plus and minus zoom. Select a car or a junction for details.`}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onClick={() => { drag.current = null; }}
      >
        <defs>
          <pattern id={hatchId} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="var(--road)" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--text-secondary)" strokeWidth="1.4" strokeOpacity="0.5" />
          </pattern>
        </defs>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {geo.isCity && (
            <g role="group" aria-label="Roads">
              {geo.junctionAreas.map((j) => <path key={`ja-${j.id}`} d={j.d} fill="var(--road)" stroke="var(--road-edge)" strokeWidth={0.6} />)}
              {geo.cityLanes.map((l) => (
                <path key={l.key} d={l.d} fill="none" strokeWidth={l.w + 0.05} strokeLinecap="butt"
                  stroke={l.type === 'local' ? 'var(--road-res)' : l.tint === 'bus' ? 'var(--bus-lane)' : 'var(--road)'}
                  strokeOpacity={l.tint === 'bus' ? 0.55 : 1}>
                  <title>{ROAD_TYPE_TEXT[l.type] ?? 'Road'}{l.tint === 'noheavy' ? ' · this lane: no trucks or buses' : ''}</title>
                </path>
              ))}
              {geo.cityLanes.filter((l) => l.tint === 'noheavy').map((l) => (
                <path key={`${l.key}-h`} d={l.d} fill="none" strokeWidth={l.w * 0.8} stroke={`url(#${hatchId})`}
                  strokeOpacity={0.5} pointerEvents="none" />
              ))}
              {geo.cityMarks.map((m) => (
                <path key={m.key} d={m.d} fill="none" stroke={m.centre ? 'var(--road-centre)' : 'var(--road-line)'}
                  strokeWidth={m.centre ? 0.3 : 0.22} strokeDasharray={m.centre ? undefined : '3 4'} pointerEvents="none" />
              ))}
              {geo.cityArrows.map((a) => (
                <path key={a.key} d="M-1.4 -0.9 L1.4 0 L-1.4 0.9" fill="none" stroke="var(--road-line)" strokeWidth={0.35}
                  transform={`translate(${a.x} ${a.y}) rotate(${a.angle})`} pointerEvents="none" />
              ))}
            </g>
          )}
          <g display={geo.isCity ? 'none' : undefined}>
            {geo.roads.map((r) => (
              <line key={`${r.key}-edge`} x1={r.x1} y1={r.y1} x2={r.x2} y2={r.y2} stroke="var(--road-edge)"
                strokeWidth={r.w + 2} strokeLinecap="butt" />
            ))}
            {geo.roads.map((r) => (
              <line key={r.key} x1={r.x1} y1={r.y1} x2={r.x2} y2={r.y2}
                stroke={r.type === 'residential' ? 'var(--road-res)' : 'var(--road)'} strokeWidth={r.w} strokeLinecap="butt">
                <title>{ROAD_TYPE_TEXT[r.type] ?? 'Road'}</title>
              </line>
            ))}
            {/* Restricted lanes: bus lanes (red) and lanes closed to trucks and buses (hatched) */}
            {geo.laneMarks.map((m) => (
              <line key={m.key} x1={m.x1} y1={m.y1} x2={m.x2} y2={m.y2} strokeWidth={m.w} strokeLinecap="butt"
                stroke={m.bus ? 'var(--bus-lane)' : `url(#${hatchId})`} strokeOpacity={m.bus ? 0.32 : 0.55} pointerEvents="none" />
            ))}
          </g>

          {/* Congestion: observed speed against the speed limit, per direction */}
          {layers.congestion && edgeSpeeds && (
            <g role="group" aria-label="Congestion">
              {Object.entries(edgeSpeeds).map(([id, v]) => {
                const e = geo.edges[id];
                if (!e) return null;
                if (!congestion(v, e.limit, e.tls ? tlsState?.[e.tls]?.queues?.[e.approach] ?? 0 : null)) return null;
                return (
                  <path key={id} d={e.d} stroke="var(--sig-yellow)" strokeOpacity={0.5}
                    strokeWidth={e.w} fill="none" strokeLinecap="butt">
                    <title>{`${id}: congested, ${(v * 3.6).toFixed(0)} km/h (limit ${(e.limit * 3.6).toFixed(0)})`}</title>
                  </path>
                );
              })}
            </g>
          )}

          <g display={geo.isCity ? 'none' : undefined}>
            {/* centre line between the two directions */}
            {geo.roads.map((r) => (
              <line key={`${r.key}-mid`} x1={r.x1} y1={r.y1} x2={r.x2} y2={r.y2} stroke="var(--road-line)"
                strokeOpacity={0.75} strokeWidth={0.9} strokeDasharray="6 5" />
            ))}
          </g>

          {/* Blocked roads: what is really blocked (solid) and what the traffic centre knows (label) */}
          {layers.incidents && incidentEdges.map((id) => {
            const e = geo.edges[id];
            const kind = actual[id] || known[id];
            const closed = kind === 'edge_blocked';
            const reported = Boolean(known[id]);
            const text = `${closed ? 'Road closed' : 'Lane blocked'}${reported ? '' : ' · not yet detected'}`;
            return (
              <g key={`inc-${id}`} className="incident">
                <path d={e.d} stroke={closed ? 'var(--sig-red)' : 'var(--sig-yellow)'} strokeOpacity={0.6} strokeWidth={e.w}
                  fill="none" strokeDasharray={closed ? '5 4' : undefined} />
                <g transform={`translate(${e.mid[0]},${e.mid[1]})`}>
                  <path d="M0 -8 L8 6 H-8 Z" fill={closed ? 'var(--sig-red)' : 'var(--sig-yellow)'} stroke="var(--surface-1)" strokeWidth={1} />
                  <path d="M0 -3.5 V1.5 M0 3.2 V4.2" stroke={closed ? '#fff' : '#111'} strokeWidth={1.5} />
                  <text y={-12} textAnchor="middle" className="map-label strong">{text}</text>
                  <title>{`${id}: ${text}${reported ? ' (reported to the traffic centre over VANET)' : ''}`}</title>
                </g>
              </g>
            );
          })}

          {geo.zone && layers.incidents && <ZoneOverlay z={geo.zone} st={zoneState} />}

          {/* Remaining route of each ambulance */}
          {layers.routes && evRoutes && Object.entries(evRoutes).map(([id, route]) => (
            <g key={`route-${id}`} className="ev-route" role="group" aria-label={`Route of ${id}`}>
              {route.map((eid) => geo.edges[eid] && (
                <path key={eid} d={geo.edges[eid].d} fill="none" stroke="var(--ev-route)" strokeWidth={2.6} strokeDasharray="7 5"
                  strokeLinecap="round" />
              ))}
            </g>
          ))}

          {/* Route explorer: the A* alternatives, the selected one on top */}
          {routeOverlay?.routes?.length > 0 && (
            <g role="group" aria-label="Route alternatives">
              {routeOverlay.routes.map((r, i) => ({ r, i }))
                .sort((a, b) => (a.i === routeOverlay.selected) - (b.i === routeOverlay.selected))
                .map(({ r, i }) => {
                  const sel = i === routeOverlay.selected;
                  return (
                    <g key={`alt-${i}`} opacity={sel ? 1 : 0.55}>
                      {r.path.map((eid) => geo.edges[eid] && (
                        <path key={eid} d={geo.edges[eid].d} fill="none" stroke={ROUTE_COLORS[i % ROUTE_COLORS.length]}
                          strokeWidth={sel ? 5 : 3} strokeLinecap="round" strokeLinejoin="round" />
                      ))}
                    </g>
                  );
                })}
            </g>
          )}

          <g>
            {geo.junctions.map((j) => {
              const sel = selectedJunction === j.id;
              const st = tlsState?.[j.id];
              const ev = st?.phase === 'EV_GREEN';
              return (
                <g key={j.id} className="junction" role="button" tabIndex={0}
                  aria-label={`Junction ${j.id}${ev ? ' ambulance priority' : st ? `, phase ${st.phase}` : ''}. Show details`} aria-pressed={sel}
                  onClick={clickUnlessDragged(() => onSelectJunction?.(sel ? null : j.id))}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); onSelectJunction?.(sel ? null : j.id); } }}>
                  {layers.range && j.R && (
                    <circle cx={j.X} cy={j.Y} r={j.R} fill="var(--rsu)" fillOpacity={sel ? 0.1 : 0.045} stroke="var(--rsu)"
                      strokeOpacity={0.6} strokeWidth={1} strokeDasharray="4 4" pointerEvents="none" />
                  )}
                  <rect x={j.X - geo.jHalf} y={j.Y - geo.jHalf} width={geo.jHalf * 2} height={geo.jHalf * 2}
                    fill="var(--junction)" stroke={ev ? 'var(--sig-red)' : sel ? 'var(--accent)' : 'none'} strokeWidth={ev || sel ? 2.5 : 0} />
                  <text x={j.X} y={j.Y + (ev ? 1 : 4.5)} textAnchor="middle" className="map-label junction-name">{j.id}</text>
                  {layers.rsu && geo.vanet?.active && (
                    <g transform={`translate(${j.X + geo.jHalf + 9},${j.Y - geo.jHalf - 9})`} pointerEvents="none">
                      <circle r={7} fill="var(--surface-1)" stroke="var(--rsu)" strokeWidth={1.2} />
                      <path d="M-4 -0.5 A5.5 5.5 0 0 1 4 -0.5 M-2.3 1.5 A3 3 0 0 1 2.3 1.5" stroke="var(--rsu)" strokeWidth={1.2} fill="none" strokeLinecap="round" />
                      <circle cy={3.4} r={0.9} fill="var(--rsu)" />
                    </g>
                  )}
                  <title>{`Junction ${j.id}${geo.vanet?.active ? ` · roadside unit, range ${geo.vanet.range_m} m` : ''}`}</title>
                </g>
              );
            })}
            {/* Ambulance note drawn beside the junction button, not inside it: the button's name says it */}
            {geo.junctions.filter((j) => tlsState?.[j.id]?.phase === 'EV_GREEN').map((j) => (
              <text key={`ev-${j.id}`} x={j.X} y={j.Y + 16} textAnchor="middle" className="map-label junction-note"
                pointerEvents="none" aria-hidden="true">AMBULANCE</text>
            ))}
          </g>

          {layers.vehicles && (
            <g>
              {rows.map((row) => {
                const [id, x, y, , flags] = row;
                const kind = vehicleKind(flags);
                const category = categoryOf(kindIdx >= 0 ? row[kindIdx] : null, flags);
                const [realL, realW] = sizeOf(category, network.vehicle_types);
                // The city map is zoomed out to show the whole ring: vehicles are drawn larger there to stay visible
                const len = geo.isCity ? realL * CITY_VEHICLE_SCALE : realL + EXTRA_L;
                const wid = geo.isCity ? realW * CITY_VEHICLE_SCALE : Math.max(2, realW * WIDTH_SCALE);
                const [px, py] = geo.pt(x, y);
                return (
                  <g
                    key={id}
                    className="car-g"
                    style={{ transform: `translate(${px}px, ${py}px) rotate(${nextHeadings.get(id)}deg)` }}
                    onClick={clickUnlessDragged(() => onSelect?.(id))}
                  >
                    <title>{`${id} – ${CATEGORY_LABEL[category]}, ${KIND_LABEL[kind].toLowerCase()}${has(flags, FLAG.ADVISED) ? ' (slowing for green)' : ''}`}</title>
                    {/* invisible larger hit area */}
                    <circle cy={len / 2} r={geo.isCity ? 4 : 8} fill="transparent" />
                    <CarBody id={id} kind={kind} category={category} len={len} w={wid} />
                    {has(flags, FLAG.ADVISED) && <circle cx={0} cy={-2} r={1.3} fill="var(--sig-yellow)" />}
                  </g>
                );
              })}
            </g>
          )}

          {/* Signals above the cars, so a queue never hides its light */}
          <g>
            {geo.heads.map((h) => {
              const st = tlsState?.[h.tls];
              const s = st?.signal?.[h.approach];
              const q = st?.queues?.[h.approach];
              const sp = st?.spat?.[h.approach];
              const secs = sp && sp.min_s != null && sp.min_s === sp.max_s ? Math.ceil(sp.min_s) : null;
              return (
                <g key={h.id}>
                  <circle cx={h.pos[0]} cy={h.pos[1]} r={5.2} fill={SIG_FILL[s] || 'var(--surface-2)'} stroke="#0b0d12" strokeWidth={1.4}>
                    <title>{`${h.tls}, from the ${APPROACH_WORD[h.approach]}: ${SIG_WORD[s] || 'unknown'}`}</title>
                  </circle>
                  {layers.timers && secs != null && s && (
                    <text x={h.timer[0]} y={h.timer[1] + 3.5} textAnchor="middle" className={`map-label timer sig-text-${s}`}>
                      {secs}s
                      <title>{`${SIG_WORD[s]} for ${secs} more seconds`}</title>
                    </text>
                  )}
                  {layers.queues && q > 0 && (
                    <g transform={`translate(${h.badge[0]},${h.badge[1]})`}>
                      <rect x={-9} y={-7} width={18} height={14} rx={7} fill="var(--surface-1)" stroke="var(--border-strong)" strokeWidth={0.8} />
                      <text y={3.6} textAnchor="middle" className="map-label queue">{q}</text>
                      <title>{`${q} vehicle${q === 1 ? '' : 's'} waiting at ${h.tls} from the ${APPROACH_WORD[h.approach]}`}</title>
                    </g>
                  )}
                </g>
              );
            })}
          </g>

          {layers.labels && Object.values(geo.edges).map((e) => (
            <text key={`n-${e.id}`} x={e.mid[0]} y={e.mid[1]} textAnchor="middle" className="map-label road-name"
              transform={e.horizontal ? undefined : `rotate(-90 ${e.mid[0]} ${e.mid[1]})`} dy={3}>{e.id}</text>
          ))}

          {layers.lengths && geo.roadLengths.map((r) => (
            <text key={`len-${r.key}`} x={r.x} y={r.y} textAnchor="middle" className="map-label road-length" dy={3}
              transform={r.horizontal ? undefined : `rotate(-90 ${r.x} ${r.y})`}>
              {r.text}<title>{r.title}</title>
            </text>
          ))}

          {followed && (() => {
            const [fx, fy] = geo.pt(followed[1], followed[2]);
            return (
              <g className="car-g" style={{ transform: `translate(${fx}px, ${fy}px)` }} pointerEvents="none">
                <circle r={12} fill="none" stroke="var(--text-primary)" strokeWidth={1.8} />
                <text y={-16} textAnchor="middle" className="map-label strong">{followed[0]}</text>
              </g>
            );
          })()}
        </g>
      </svg>
      {/* bottom right of the map itself, so it never covers the legend below */}
      <div className="map-chip">
        {network.vanet?.active
          ? `VANET: ${connected} of ${rows.length} vehicles connected · penetration ${Math.round((network.vanet.penetration ?? 0) * 100)} %`
          : `No VANET in this run · ${rows.length} vehicles`}
      </div>
      </div>

      {stale && <div className="map-stale" role="status">{stale}</div>}

      <div className="map-controls" role="group" aria-label="Map controls">
        <button type="button" className="map-btn" onClick={() => zoomAt(1.4, geo.width / 2, geo.height / 2)} aria-label="Zoom in"
          disabled={view.k >= maxZoom}><Icon name="plus" /></button>
        <button type="button" className="map-btn" onClick={() => zoomAt(1 / 1.4, geo.width / 2, geo.height / 2)} aria-label="Zoom out"
          disabled={!zoomed}><Icon name="minus" /></button>
        <button type="button" className="map-btn" onClick={reset} aria-label="Fit the whole network" disabled={!zoomed}><Icon name="fit" /></button>
        <button type="button" className={`map-btn${showLayers ? ' active' : ''}`} onClick={() => setShowLayers((s) => !s)}
          aria-label="Map layers" aria-expanded={showLayers}><Icon name="layers" /></button>
      </div>
      {showLayers && (
        <fieldset className="map-layers">
          <legend>Layers</legend>
          {LAYERS.map((l) => (
            <label key={l.id}><input type="checkbox" checked={Boolean(layers[l.id])} onChange={() => toggleLayer(l.id)} /> {l.label}</label>
          ))}
          <p className="small muted">Ctrl + scroll or the buttons zoom; drag moves the map.</p>
        </fieldset>
      )}

      <details className="map-legend-box" open={typeof window === 'undefined' || window.innerWidth > 700}>
        <summary>Legend</summary>
        <div className="map-legend" aria-label="Map legend">
          <span className="legend-cars">
            <CarIcon style="sedan" paint="#c8302c" /><CarIcon style="suv" paint="#c4c7cb" />
            <CarIcon style="pickup" paint="#2f6fd0" /><CarIcon style="hatchback" paint="#f0c419" />
            Cars (colour and body style only tell them apart)
          </span>
          <span><CarIcon kind="eco" category="eco_car" />Eco vehicle (always this green)</span>
          {geo.isCity && (
            <>
              <span><CarIcon kind="plain" category="motorcycle" len={3.7} w={2.1} />Motorcycle</span>
              <span><CarIcon kind="plain" category="bus" len={13.5} w={5.9} size={30} />Bus</span>
              <span><CarIcon kind="plain" category="truck" len={11.5} w={5.8} size={28} />Truck</span>
            </>
          )}
          <span><svg width="10" height="10"><circle cx="5" cy="5" r="3.5" fill="var(--series-1)" stroke="#fff" strokeWidth="1" /></svg>Roof dot: VANET radio (no dot: not connected)</span>
          <span><CarIcon kind="emergency" len={8} />Ambulance</span>
          <span><svg width="26" height="8"><path d="M1 4 H25" stroke="var(--ev-route)" strokeWidth="2.6" strokeDasharray="7 5" /></svg>Ambulance route</span>
          <span><CarIcon kind="driven" />Driven by a viewer</span>
          <span><svg width="12" height="12"><circle cx="6" cy="6" r="4.6" fill="var(--sig-green)" stroke="#0b0d12" strokeWidth="1.2" /></svg>Signal (green / yellow / red) with seconds left</span>
          <span><svg width="20" height="14"><rect x="1" y="1" width="18" height="12" rx="6" fill="var(--surface-1)" stroke="var(--border-strong)" /><text x="10" y="10.5" textAnchor="middle" fontSize="9" fill="var(--text-primary)">5</text></svg>Vehicles waiting</span>
          <span><svg width="22" height="10"><rect x="1" y="2" width="20" height="6" fill="var(--sig-yellow)" fillOpacity="0.5" /></svg>Congested road</span>
          <span><svg width="14" height="14"><path d="M7 1 L13 13 H1 Z" fill="var(--sig-red)" /><path d="M7 5 V9 M7 10.5 V11.5" stroke="#fff" strokeWidth="1.4" /></svg>Accident / road closed</span>
          <span><svg width="16" height="16"><circle cx="8" cy="8" r="6.5" fill="var(--surface-1)" stroke="var(--rsu)" strokeWidth="1.2" /><path d="M4.5 7.5 A5 5 0 0 1 11.5 7.5 M6 9.3 A2.8 2.8 0 0 1 10 9.3" stroke="var(--rsu)" strokeWidth="1.2" fill="none" /></svg>Roadside unit (RSU)</span>
          {geo.zone && <span><svg width="22" height="10"><rect x="1" y="2" width="20" height="6" fill="var(--zone)" fillOpacity="0.45" /></svg>Shared lane, one direction at a time</span>}
          {geo.isCity && (
            <>
              <span><svg width="26" height="14"><rect x="1" y="1" width="24" height="12" fill="var(--road)" /><path d="M1 5 H25 M1 9 H25" stroke="var(--road-line)" strokeDasharray="3 3" /></svg>Main road (3 lanes each way)</span>
              <span><svg width="26" height="10"><rect x="1" y="1" width="24" height="8" fill="var(--road)" /><path d="M1 5 H25" stroke="var(--road-line)" strokeDasharray="3 3" /></svg>Secondary / collector road (2 lanes each way)</span>
              <span><svg width="26" height="8"><rect x="1" y="1" width="24" height="6" fill="var(--road-res)" /></svg>Local sub-road (1 lane, 30 km/h, no trucks or buses)</span>
              <span><svg width="22" height="10"><rect x="1" y="2" width="20" height="6" fill={`url(#${hatchId})`} stroke="var(--border-strong)" strokeWidth="0.5" /></svg>Lane closed to trucks and buses (no left turns for them at the signals)</span>
              {geo.cityLanes.some((l) => l.tint === 'bus') && (
                <span><svg width="22" height="10"><rect x="1" y="2" width="20" height="6" fill="var(--bus-lane)" fillOpacity="0.4" /></svg>Bus lane (buses and ambulances)</span>
              )}
            </>
          )}
          {routeOverlay?.routes?.length > 0 && routeOverlay.routes.map((r, i) => (
            <span key={`lr-${i}`}><svg width="26" height="8"><path d="M1 4 H25" stroke={ROUTE_COLORS[i % ROUTE_COLORS.length]} strokeWidth="3" /></svg>
              Route {i + 1}{r.fastest ? ' (fastest now)' : ''}{r.shortest ? ' (shortest)' : ''}</span>
          ))}
          <span><svg width="10" height="10"><circle cx="5" cy="5" r="3" fill="var(--sig-yellow)" /></svg>Car slowing for green</span>
        </div>
      </details>
    </div>
  );
}

export default memo(TrafficMapInner);

/** Zone drawing data (network message "zone") in map coordinates. */
function zoneGeometry(zone, pt) {
  if (!zone) return null;
  const P = ([x, y]) => pt(x, y);
  const path = (pts) => pts.map(P).map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
  const shared = zone.shared.map(P);
  return {
    sharedPath: path(zone.shared),
    sharedStart: shared[0], // west end: eastbound traffic enters here
    sharedEnd: shared[shared.length - 1],
    blocked: zone.blocked.map(path),
    crash: zone.crash.map(P),
    debris: P(zone.debris),
    closure: zone.closure.map(P),
    entry: Object.fromEntries(Object.entries(zone.entry).map(([d, e]) => [d, { head: P(e.head), line: e.line.map(P), label: e.label }])),
  };
}

const DIR_WORD = { E: 'eastbound', W: 'westbound' };

/** Accident, shared lane with the current direction of flow, entry signals and the closure at J3. */
function ZoneOverlay({ z, st }) {
  if (!st) return null;
  const alt = st.mode === 'alternating';
  const closure = st.mode === 'closure';
  const crashShown = st.accident || alt || closure;
  const flowing = alt && (st.phase === 'green' || st.phase === 'yellow') ? st.dir : null;
  const [ax, ay] = flowing === 'W' ? z.sharedEnd : z.sharedStart;
  const [bx, by] = flowing === 'W' ? z.sharedStart : z.sharedEnd;
  const chevrons = [];
  if (flowing) {
    const s = bx > ax ? 1 : -1;
    for (let k = 1; k <= 3; k += 1) {
      const x = ax + ((bx - ax) * k) / 4;
      const y = ay + ((by - ay) * k) / 4;
      chevrons.push(`M${x - 3 * s} ${y - 3} L${x} ${y} L${x - 3 * s} ${y + 3}`);
    }
  }
  const midX = (z.sharedStart[0] + z.sharedEnd[0]) / 2;
  return (
    <g role="group" aria-label="Accident zone">
      {crashShown && z.blocked.map((d, i) => (
        <path key={`b${i}`} d={d} stroke="var(--sig-red)" strokeOpacity={0.3} strokeWidth={8} fill="none" />
      ))}
      {alt && (
        <path d={z.sharedPath} stroke="var(--zone)" strokeOpacity={st.phase === 'clear' ? 0.25 : 0.45} strokeWidth={8} fill="none">
          <title>{`Shared lane: ${st.phase === 'clear' ? 'clearing, both sides red' : `${DIR_WORD[st.dir]} turn`}`}</title>
        </path>
      )}
      {chevrons.map((d, i) => <path key={`c${i}`} d={d} stroke="var(--zone-strong)" strokeWidth={1.6} fill="none" />)}
      {alt && (
        <text x={midX} y={z.sharedStart[1] + 17} textAnchor="middle" className="map-label strong">
          {st.phase === 'clear' ? 'Shared lane: clearing' : `Shared lane: ${DIR_WORD[st.dir]} turn`}
        </text>
      )}
      {crashShown && (
        <>
          {z.crash.map(([x, y], i) => (
            <g key={`x${i}`} transform={`translate(${x},${y})`}>
              <path d="M0 -6 L6 5 H-6 Z" fill="var(--sig-red)" stroke="var(--surface-1)" strokeWidth={0.8} />
              <path d="M0 -2.5 V1.5 M0 2.8 V3.6" stroke="#fff" strokeWidth={1.3} />
              <title>Accident: westbound lanes blocked</title>
            </g>
          ))}
          <g transform={`translate(${z.debris[0]},${z.debris[1]})`}>
            <path d="M0 -3.5 L3.5 3 H-3.5 Z" fill="var(--sig-yellow)" stroke="var(--surface-1)" strokeWidth={0.6} />
            <title>Debris on the inner eastbound lane</title>
          </g>
        </>
      )}
      {alt && ['E', 'W'].map((d) => {
        const e = z.entry[d];
        const s = st.signal?.[d];
        const [[x1, y1], [x2, y2]] = e.line;
        return (
          <g key={`e${d}`}>
            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={s === 'G' ? 'var(--sig-green)' : 'var(--sig-red)'} strokeWidth={1.6} />
            <circle cx={e.head[0]} cy={e.head[1]} r={5.2} fill={SIG_FILL[s] || 'var(--surface-2)'} stroke="#0b0d12" strokeWidth={1.4}>
              <title>{`Zone signal for ${e.label} traffic: ${SIG_WORD[s] || 'off'}`}</title>
            </circle>
          </g>
        );
      })}
      {closure && (
        <line x1={z.closure[0][0]} y1={z.closure[0][1]} x2={z.closure[1][0]} y2={z.closure[1][1]}
          stroke="var(--sig-red)" strokeWidth={4}>
          <title>Westbound road closed at J3</title>
        </line>
      )}
    </g>
  );
}
