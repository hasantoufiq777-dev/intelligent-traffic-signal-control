// Driver perspective: a 3D view from the driver's seat (or just behind) of one vehicle in the live run.
// Everything comes from the simulation's messages: lane shapes and widths, lane permissions, signal
// states, incidents and every vehicle's position, heading, category and size. Positions arrive once a
// second and are interpolated, like the map. three.js is loaded only when this view is opened.
import { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { fmt, isNum } from '../lib/format.js';
import { categoryOf, CATEGORY_LABEL, ECO_COLOR, fieldIndex, FLAG, has, sizeOf } from '../lib/vehicles.js';

const PAINT = ['#f2f2ef', '#c4c7cb', '#2a2c30', '#c8302c', '#2f6fd0', '#22406e', '#f0c419', '#e8772e', '#1e9aa0',
  '#b08a5a', '#7a2e3a'];
const SIG = { G: '#22c55e', g: '#22c55e', y: '#facc15', r: '#ef4444' };
const SIG_OFF = '#1f2937';
const FRAME_MS = 1000; // simulated frames arrive once a second

function hash(id) {
  let h = 2166136261;
  for (const ch of String(id)) {
    h ^= ch.charCodeAt(0);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** SUMO (x, y, heading in degrees clockwise from north) -> three.js (x right, z south, y up). */
const P = (x, y, h = 0) => new THREE.Vector3(x, h, -y);
const yawOf = (deg) => -THREE.MathUtils.degToRad(deg); // rotation about y; 0 deg = facing -z (north)

/** Triangles of a flat ribbon along a polyline (SUMO coords), width w, at height y. */
function ribbon(points, w, y, out) {
  if (points.length < 2) return;
  const half = w / 2;
  const left = [];
  const right = [];
  for (let i = 0; i < points.length; i += 1) {
    const a = points[Math.max(0, i - 1)];
    const b = points[Math.min(points.length - 1, i + 1)];
    let dx = b[0] - a[0];
    let dy = b[1] - a[1];
    const L = Math.hypot(dx, dy) || 1;
    dx /= L;
    dy /= L;
    const nx = -dy; // left normal in SUMO coords
    const ny = dx;
    left.push([points[i][0] + nx * half, points[i][1] + ny * half]);
    right.push([points[i][0] - nx * half, points[i][1] - ny * half]);
  }
  for (let i = 0; i < points.length - 1; i += 1) {
    const [l0, l1, r0, r1] = [left[i], left[i + 1], right[i], right[i + 1]];
    out.push(l0[0], y, -l0[1], r0[0], y, -r0[1], l1[0], y, -l1[1]);
    out.push(r0[0], y, -r0[1], r1[0], y, -r1[1], l1[0], y, -l1[1]);
  }
}

/** Points of a polyline offset sideways (left positive), SUMO coords. */
function offsetLine(points, off) {
  return points.map((p, i) => {
    const a = points[Math.max(0, i - 1)];
    const b = points[Math.min(points.length - 1, i + 1)];
    const L = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    return [p[0] - ((b[1] - a[1]) / L) * off, p[1] + ((b[0] - a[0]) / L) * off];
  });
}

/** Dashes (length d, gap g) along a polyline, as short polylines. */
function dashes(points, d = 3, g = 6) {
  const out = [];
  let carry = 0;
  for (let i = 0; i < points.length - 1; i += 1) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[i + 1];
    const L = Math.hypot(x1 - x0, y1 - y0);
    for (let s = carry; s < L; s += d + g) {
      const e = Math.min(L, s + d);
      out.push([[x0 + ((x1 - x0) * s) / L, y0 + ((y1 - y0) * s) / L], [x0 + ((x1 - x0) * e) / L, y0 + ((y1 - y0) * e) / L]]);
      carry = s + d + g - L;
    }
  }
  return out;
}

function meshFrom(positions, color) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  g.computeVertexNormals();
  return new THREE.Mesh(g, new THREE.MeshLambertMaterial({ color, side: THREE.DoubleSide }));
}

function approachOf(shape) {
  const [x1, y1] = shape[shape.length - 2];
  const [x2, y2] = shape[shape.length - 1];
  if (Math.abs(y2 - y1) >= Math.abs(x2 - x1)) return y2 > y1 ? 'S' : 'N';
  return x2 > x1 ? 'W' : 'E';
}

/** Static scene: ground, roads, markings, junction areas, buildings, signal heads. */
function buildStatic(network) {
  const group = new THREE.Group();
  const [xmin, ymin, xmax, ymax] = network.bounds ?? [0, 0, 1000, 1000];
  const cx = (xmin + xmax) / 2;
  const cy = (ymin + ymax) / 2;
  const span = Math.max(xmax - xmin, ymax - ymin) + 1200;
  // Layers are kept clearly apart in height (ground -0.25 m, junctions -0.05, roads 0, markings 0.04) and
  // the ground is pushed back in depth: with a coarse depth buffer (some GPUs, software rendering) layers a
  // few centimetres apart flicker or the grass shows through the road.
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(span, span),
    new THREE.MeshLambertMaterial({ color: '#7f9a6b', polygonOffset: true, polygonOffsetFactor: 4, polygonOffsetUnits: 4 }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.set(cx, -0.25, -cy);
  group.add(ground);

  const asphalt = [];
  const residential = [];
  const bus = [];
  const noHeavy = [];
  const white = [];
  const centre = [];
  const segs = [];
  for (const e of network.edges) {
    const lanes = e.lane_info?.length ? e.lane_info
      : Array.from({ length: Math.max(1, e.lanes) }, (_, i) => ({ w: 3.2, allow: null, shape: null, i }));
    lanes.forEach((l, i) => {
      const shape = l.shape ?? offsetLine(e.shape, -((lanes.length - 1 - i) * 3.2 + 1.6));
      const target = e.type === 'residential' ? residential
        : l.allow && l.allow.includes('bus') && !l.allow.includes('passenger') ? bus
          : l.allow ? noHeavy : asphalt;
      ribbon(shape, l.w || 3.2, 0, target);
      segs.push(shape);
      if (i < lanes.length - 1) {
        for (const d of dashes(offsetLine(shape, (l.w || 3.2) / 2))) ribbon(d, 0.15, 0.04, white);
      } else {
        ribbon(offsetLine(shape, (l.w || 3.2) / 2 - 0.1), 0.15, 0.04, centre);
      }
    });
  }
  group.add(meshFrom(asphalt, '#3b3f45'), meshFrom(residential, '#62666b'), meshFrom(bus, '#7c2d2a'),
    meshFrom(noHeavy, '#454a52'), meshFrom(white, '#f4f4f4'), meshFrom(centre, '#f4f4f4'));

  // Junction areas: SUMO's junction shape when the road map has it, otherwise an asphalt disc
  for (const j of network.junctions) {
    let mesh;
    if (j.shape?.length > 2) {
      const outline = new THREE.Shape(j.shape.map(([x, y]) => new THREE.Vector2(x, y)));
      mesh = new THREE.Mesh(new THREE.ShapeGeometry(outline), new THREE.MeshLambertMaterial({ color: '#3b3f45', side: THREE.DoubleSide }));
      mesh.rotation.x = -Math.PI / 2; // shape x,y -> world x,-z
      mesh.position.y = -0.05;
    } else {
      mesh = new THREE.Mesh(new THREE.CircleGeometry(j.tls ? 15 : 11, 24), new THREE.MeshLambertMaterial({ color: '#3b3f45' }));
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(j.x, -0.05, -j.y);
    }
    group.add(mesh);
  }

  // Buildings on free ground, for depth (deterministic positions and heights)
  const near = (x, y) => segs.some((s) => s.some(([px, py], k) => {
    if (k === 0) return false;
    const [qx, qy] = s[k - 1];
    const vx = px - qx;
    const vy = py - qy;
    const t = Math.max(0, Math.min(1, ((x - qx) * vx + (y - qy) * vy) / (vx * vx + vy * vy || 1)));
    return Math.hypot(x - (qx + t * vx), y - (qy + t * vy)) < 26;
  }));
  const boxes = [];
  for (let x = xmin - 120; x <= xmax + 120; x += 55) {
    for (let y = ymin - 120; y <= ymax + 120; y += 55) {
      const h = hash(`${x},${y}`);
      if (h % 3 === 0 || near(x, y)) continue;
      boxes.push({ x: x + ((h >>> 4) % 15) - 7, y: y + ((h >>> 9) % 15) - 7, w: 16 + (h % 14), d: 16 + ((h >>> 5) % 14), h: 7 + ((h >>> 11) % 26) });
    }
  }
  if (boxes.length) {
    const inst = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshLambertMaterial({ color: '#b9b2a6' }), boxes.length);
    const m = new THREE.Matrix4();
    boxes.forEach((b, i) => {
      m.compose(new THREE.Vector3(b.x, b.h / 2, -b.y), new THREE.Quaternion(), new THREE.Vector3(b.w, b.h, b.d));
      inst.setMatrixAt(i, m);
      inst.setColorAt(i, new THREE.Color().setHSL(((hash(i) % 60) + 20) / 360, 0.12, 0.62 + ((hash(i) >>> 8) % 20) / 100));
    });
    group.add(inst);
  }

  // Signal heads at the stop line, right of the lanes
  const tls = new Set(network.junctions.filter((j) => j.tls).map((j) => j.id));
  const heads = [];
  const pole = new THREE.CylinderGeometry(0.12, 0.12, 4.2, 8);
  const box = new THREE.BoxGeometry(0.5, 1.4, 0.4);
  const bulb = new THREE.SphereGeometry(0.17, 12, 8);
  for (const e of network.edges) {
    if (!tls.has(e.to) || e.shape.length < 2) continue;
    const [x1, y1] = e.shape[e.shape.length - 2];
    const [x2, y2] = e.shape[e.shape.length - 1];
    const L = Math.hypot(x2 - x1, y2 - y1) || 1;
    const ux = (x2 - x1) / L;
    const uy = (y2 - y1) / L;
    const w = (e.lane_info ?? []).reduce((t, l) => t + (l.w || 3.2), 0) || Math.max(1, e.lanes) * 3.2;
    const sx = x2 + uy * (w + 0.8); // right of the road (SUMO right normal = (uy, -ux))
    const sy = y2 - ux * (w + 0.8);
    const g = new THREE.Group();
    const p = new THREE.Mesh(pole, new THREE.MeshLambertMaterial({ color: '#4b5563' }));
    p.position.y = 2.1;
    const b = new THREE.Mesh(box, new THREE.MeshLambertMaterial({ color: '#111827' }));
    b.position.y = 4.5;
    g.add(p, b);
    const bulbs = {};
    [['r', 0.45], ['y', 0], ['G', -0.45]].forEach(([k, dy]) => {
      const m = new THREE.Mesh(bulb, new THREE.MeshBasicMaterial({ color: SIG_OFF }));
      m.position.set(0, 4.5 + dy, 0.22);
      g.add(m);
      bulbs[k] = m;
    });
    g.position.set(sx, 0, -sy);
    g.rotation.y = Math.atan2(-ux, uy) + Math.PI; // face the approaching drivers
    group.add(g);
    heads.push({ tls: e.to, approach: approachOf(e.shape), bulbs });
  }
  return { group, heads };
}

/** A vehicle model of real size, pointing to -z, origin at its centre on the ground. */
function vehicleModel(category, size, paint) {
  const [L, W, H] = size;
  const g = new THREE.Group();
  const mat = (c) => new THREE.MeshLambertMaterial({ color: c });
  const add = (w, h, l, c, y, z = 0) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, l), mat(c));
    m.position.set(0, y, z);
    g.add(m);
    return m;
  };
  if (category === 'motorcycle') {
    add(W * 0.5, H * 0.45, L, paint, H * 0.3);
    const rider = new THREE.Mesh(new THREE.SphereGeometry(0.28, 12, 8), mat('#1f2937'));
    rider.position.set(0, H, 0.1);
    g.add(rider);
  } else if (category === 'truck') {
    add(W, H * 0.8, 2.4, paint, H * 0.4, -L / 2 + 1.2);
    add(W, H, L - 2.6, '#d7d9dc', H / 2 + 0.05, 1.3);
  } else if (category === 'bus') {
    add(W, H, L, paint, H / 2);
    add(W + 0.02, H * 0.3, L * 0.9, '#1c2833', H * 0.66);
  } else if (category === 'emergency') {
    add(W, H, L, '#ffffff', H / 2);
    add(W + 0.02, 0.3, L * 0.95, '#d03b3b', H * 0.45);
    add(W * 0.7, 0.18, 0.5, '#2563eb', H + 0.09, -L / 2 + 1.2);
  } else {
    add(W, H * 0.5, L, paint, H * 0.28);
    add(W * 0.88, H * 0.42, L * 0.5, '#1c2833', H * 0.72, 0.15);
  }
  return g;
}

export default function DriverView3D({ network, vehicles, tlsState, incidents, egoId, detail, drive }) {
  const mountRef = useRef(null);
  const stateRef = useRef(null);
  const [mode, setMode] = useState('seat'); // seat | behind
  const modeRef = useRef(mode);
  modeRef.current = mode;

  // Scene, renderer and static world, rebuilt when the road map changes
  useEffect(() => {
    const el = mountRef.current;
    if (!el || !network) return undefined;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color('#a9cbe8');
    scene.fog = new THREE.Fog('#a9cbe8', 180, 650);
    scene.add(new THREE.HemisphereLight('#ffffff', '#5b6b4a', 1.0));
    const sun = new THREE.DirectionalLight('#ffffff', 0.9);
    sun.position.set(200, 300, 100);
    scene.add(sun);
    const camera = new THREE.PerspectiveCamera(70, 1, 0.5, 1500);
    const world = buildStatic(network);
    scene.add(world.group);
    const cars = new THREE.Group();
    scene.add(cars);
    const obstacles = new THREE.Group();
    scene.add(obstacles);
    const st = { renderer, scene, camera, world, cars, obstacles, models: new Map(), raf: 0, cam: null };
    stateRef.current = st;

    const resize = () => {
      const w = el.clientWidth || 640;
      const h = el.clientHeight || 360;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);

    const tick = () => {
      st.raf = requestAnimationFrame(tick);
      const now = performance.now();
      for (const m of st.models.values()) {
        const a = Math.min(1, (now - m.t0) / FRAME_MS);
        m.obj.position.lerpVectors(m.from, m.to, a);
        m.obj.rotation.y = m.yaw0 + (m.yaw1 - m.yaw0) * a;
      }
      const ego = st.models.get(st.egoId);
      if (ego) {
        const yaw = ego.obj.rotation.y;
        const fwd = new THREE.Vector3(-Math.sin(yaw), 0, -Math.cos(yaw));
        const [L, , H] = ego.size;
        let pos;
        let look;
        if (modeRef.current === 'seat') {
          pos = ego.obj.position.clone().addScaledVector(fwd, L * 0.12).setY(Math.max(1.15, H * 0.82));
          look = pos.clone().addScaledVector(fwd, 30).setY(pos.y - 0.6);
          ego.obj.visible = false;
        } else {
          pos = ego.obj.position.clone().addScaledVector(fwd, -(L * 0.5 + 9)).setY(H + 3.2);
          look = ego.obj.position.clone().addScaledVector(fwd, 18).setY(1.2);
          ego.obj.visible = true;
        }
        if (!st.cam) st.cam = { pos: pos.clone(), look: look.clone() };
        st.cam.pos.lerp(pos, 0.2);
        st.cam.look.lerp(look, 0.2);
        camera.position.copy(st.cam.pos);
        camera.lookAt(st.cam.look);
      } else {
        const [xmin, ymin, xmax, ymax] = network.bounds ?? [0, 0, 500, 500];
        camera.position.set((xmin + xmax) / 2, 380, -(ymin - 220));
        camera.lookAt((xmin + xmax) / 2, 0, -(ymin + ymax) / 2);
      }
      renderer.render(scene, camera);
    };
    tick();

    return () => {
      cancelAnimationFrame(st.raf);
      ro.disconnect();
      scene.traverse((o) => {
        o.geometry?.dispose?.();
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        mats.forEach((m) => m?.dispose?.());
      });
      renderer.dispose();
      renderer.domElement.remove();
      stateRef.current = null;
    };
  }, [network]);

  // Vehicles: one model per vehicle, moved to each new frame over FRAME_MS
  useEffect(() => {
    const st = stateRef.current;
    if (!st || !vehicles) return;
    st.egoId = egoId;
    const kindIdx = fieldIndex(vehicles, 'kind');
    const seen = new Set();
    const now = performance.now();
    for (const row of vehicles.rows ?? []) {
      const [id, x, y, angle, flags] = row; // compact map row: id, x, y, angle, flags, kind
      seen.add(id);
      const category = categoryOf(kindIdx >= 0 ? row[kindIdx] : null, flags);
      const size = sizeOf(category, network?.vehicle_types);
      const yaw = yawOf(angle);
      // SUMO's position is the front bumper; the model's origin is its centre
      const centre = P(x - Math.sin(THREE.MathUtils.degToRad(angle)) * size[0] / 2,
        y - Math.cos(THREE.MathUtils.degToRad(angle)) * size[0] / 2, 0);
      let m = st.models.get(id);
      if (!m) {
        const driven = has(flags, FLAG.DRIVER);
        const paint = driven ? '#8a44d6' : category === 'eco_car' ? ECO_COLOR : category === 'bus' ? '#f2b418'
          : PAINT[hash(id) % PAINT.length];
        const obj = vehicleModel(category, size, paint);
        obj.position.copy(centre);
        obj.rotation.y = yaw;
        st.cars.add(obj);
        m = { obj, from: centre.clone(), to: centre.clone(), yaw0: yaw, yaw1: yaw, t0: now, size };
        st.models.set(id, m);
      } else {
        m.from.copy(m.obj.position);
        m.to.copy(centre);
        m.yaw0 = m.obj.rotation.y;
        let d = yaw - m.yaw0;
        d = Math.atan2(Math.sin(d), Math.cos(d)); // turn the short way
        m.yaw1 = m.yaw0 + d;
        m.t0 = now;
      }
    }
    for (const [id, m] of st.models) {
      if (seen.has(id)) continue;
      st.cars.remove(m.obj);
      m.obj.traverse((o) => { o.geometry?.dispose?.(); o.material?.dispose?.(); });
      st.models.delete(id);
    }
  }, [vehicles, egoId, network]);

  // Signal colours
  useEffect(() => {
    const st = stateRef.current;
    if (!st) return;
    for (const h of st.world.heads) {
      const s = tlsState?.[h.tls]?.signal?.[h.approach];
      const on = s === 'g' ? 'G' : s;
      for (const [k, m] of Object.entries(h.bulbs)) m.material.color.set(k === on ? SIG[k] : SIG_OFF);
    }
  }, [tlsState]);

  // Obstacles: blocked roads from the incident layer (what is really on the road)
  useEffect(() => {
    const st = stateRef.current;
    if (!st || !network) return;
    st.obstacles.clear();
    const byId = Object.fromEntries(network.edges.map((e) => [e.id, e]));
    for (const [eid, kind] of Object.entries(incidents?.actual ?? {})) {
      const e = byId[eid];
      if (!e?.shape?.length) continue;
      const lanes = kind === 'edge_blocked' ? (e.lane_info ?? []) : (e.lane_info ?? []).slice(0, 1);
      (lanes.length ? lanes : [{ shape: e.shape, w: 3.2 }]).forEach((l) => {
        const s = l.shape ?? e.shape;
        const mid = s[Math.floor(s.length / 2)];
        const bar = new THREE.Mesh(new THREE.BoxGeometry(l.w || 3.2, 1.0, 0.6),
          new THREE.MeshLambertMaterial({ color: '#ef4444' }));
        const [x1, y1] = s[0];
        const [x2, y2] = s[s.length - 1];
        bar.position.set(mid[0], 0.5, -mid[1]);
        bar.rotation.y = Math.atan2(x2 - x1, y2 - y1);
        st.obstacles.add(bar);
      });
    }
  }, [incidents, network]);

  // Head-up display from the followed vehicle's detail row (sent to its followers every second)
  const hud = useMemo(() => {
    if (!egoId) return null;
    if (!detail || detail.id !== egoId || detail.gone) {
      // Your own vehicle: the driver messages carry speed, limit and the light ahead as well
      if (drive && drive.vid === egoId && drive.status === 'driving') {
        return {
          id: egoId, category: drive.kind ?? 'car', speed: drive.speed_kmh ?? 0, limit: drive.limit_kmh ?? null,
          tls: drive.tls, dist: drive.dist, lane: null, signal: drive.advice?.signal ?? null,
        };
      }
      return null;
    }
    return {
      id: egoId, category: categoryOf(detail.kind, detail.flags), speed: (detail.speed ?? 0) * 3.6,
      limit: isNum(detail.limit) ? detail.limit * 3.6 : null, tls: detail.tls, dist: detail.dist,
      approach: detail.approach, lane: detail.lane,
      signal: detail.tls ? tlsState?.[detail.tls]?.signal?.[detail.approach] : null,
    };
  }, [detail, egoId, tlsState, drive]);

  const eco = drive?.eco_speed;
  return (
    <div className="driver-view">
      <div ref={mountRef} className="driver-canvas" role="img"
        aria-label={hud ? `Driver perspective of ${hud.id}: ${fmt(hud.speed, 0)} km/h` : 'Driver perspective: no vehicle selected'} />
      {!hud && (
        <div className="driver-empty" role="status">
          Select a vehicle on the traffic control view, or take a car in the Vehicle tab, to see the road from its seat.
        </div>
      )}
      {hud && (
        <div className="driver-hud" aria-live="off">
          <div className="hud-speed"><span className="num">{fmt(hud.speed, 0)}</span><span className="small">km/h</span></div>
          {isNum(hud.limit) && <div className="hud-limit" title="Speed limit">{Math.round(hud.limit)}</div>}
          <div className="hud-info small">
            <div>{CATEGORY_LABEL[hud.category]} {hud.id}{isNum(hud.lane) ? ` · lane ${hud.lane + 1}` : ''}</div>
            {hud.tls ? (
              <div>
                <span className={`hud-sig sig-${hud.signal || 'off'}`} aria-hidden="true" />
                {' '}{hud.tls} in {fmt(hud.dist, 0)} m: {hud.signal === 'G' || hud.signal === 'g' ? 'green' : hud.signal === 'y' ? 'yellow' : hud.signal === 'r' ? 'red' : '–'}
              </div>
            ) : <div>No signal ahead on the route</div>}
            {eco && (
              <div>Efficient speed: <strong>{fmt(eco.target_kmh, 0)} km/h</strong> ({eco.action})</div>
            )}
          </div>
        </div>
      )}
      <div className="driver-cam" role="group" aria-label="Camera">
        <button type="button" className={`btn${mode === 'seat' ? ' primary' : ''}`} aria-pressed={mode === 'seat'} onClick={() => setMode('seat')}>Driver&apos;s seat</button>
        <button type="button" className={`btn${mode === 'behind' ? ' primary' : ''}`} aria-pressed={mode === 'behind'} onClick={() => setMode('behind')}>Behind the vehicle</button>
      </div>
    </div>
  );
}
