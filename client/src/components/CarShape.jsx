// Top-down vehicle drawings for the traffic map. Local frame: the front bumper is at y = 0 and the body
// runs backwards to y = len (SUMO's position is the front bumper); x is across, centred on 0.
// Shapes follow the vehicle category (car, motorcycle, bus, truck, ambulance). A car's paint and body
// style are picked from its id, so it keeps its look for the whole run; they only tell cars apart.
// Every eco vehicle has one fixed colour (--eco). A blue roof dot marks a vehicle with a VANET radio.

const PAINT = [
  '#f2f2ef', // white
  '#c4c7cb', // silver
  '#2a2c30', // black
  '#c8302c', // red
  '#2f6fd0', // blue
  '#22406e', // navy
  '#f0c419', // yellow
  '#e8772e', // orange
  '#2e8b57', // green
  '#1e9aa0', // teal
  '#b08a5a', // beige
  '#7a2e3a', // maroon
];
export const BODY_STYLES = ['sedan', 'hatchback', 'suv', 'pickup', 'sports'];
const GLASS = '#1c2833';
const OUTLINE = 'var(--car-outline)'; // dark in the light theme, light in the dark theme

/** FNV-1a hash of the id: stable across frames and reloads. */
function hash(id) {
  let h = 2166136261;
  const s = String(id);
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export function carLook(id) {
  const h = hash(id);
  return { paint: PAINT[h % PAINT.length], style: BODY_STYLES[(h >>> 8) % BODY_STYLES.length] };
}

/** Trapezoid window across the car between y0 and y1, half-widths w0 at y0 and w1 at y1. */
const glass = (y0, y1, w0, w1) => `M${-w0} ${y0} L${w0} ${y0} L${w1} ${y1} L${-w1} ${y1} Z`;

function Body({ style, paint, len, w }) {
  const hw = w / 2;
  const body = (rx, width = w) => (
    <rect x={-width / 2} y={0} width={width} height={len} rx={rx} fill={paint} stroke={OUTLINE} strokeWidth={0.4} />
  );
  switch (style) {
    case 'hatchback':
      return (
        <>
          {body(1.4)}
          <path d={glass(1.6, 2.5, hw - 0.4, hw - 0.6)} fill={GLASS} />
          <path d={glass(len - 1.1, len - 0.5, hw - 0.6, hw - 0.4)} fill={GLASS} />
        </>
      );
    case 'suv':
      return (
        <>
          {body(0.7)}
          <path d={glass(1.5, 2.3, hw - 0.35, hw - 0.5)} fill={GLASS} />
          <path d={glass(len - 0.9, len - 0.45, hw - 0.5, hw - 0.45)} fill={GLASS} />
          {/* roof rails */}
          <path d={`M${-hw + 0.55} 2.8 V${len - 1.3} M${hw - 0.55} 2.8 V${len - 1.3}`} stroke={OUTLINE} strokeWidth={0.3} />
        </>
      );
    case 'pickup':
      return (
        <>
          {body(0.6)}
          <path d={glass(1.4, 2.2, hw - 0.35, hw - 0.5)} fill={GLASS} />
          <path d={glass(3.0, 3.3, hw - 0.5, hw - 0.5)} fill={GLASS} />
          {/* open load bed */}
          <rect x={-hw + 0.45} y={3.75} width={w - 0.9} height={len - 4.2} rx={0.2} fill="rgba(0,0,0,0.28)" />
        </>
      );
    case 'sports': {
      const sw = w - 0.4;
      return (
        <>
          {body(1.6, sw)}
          <path d={glass(2.4, 3.3, sw / 2 - 0.45, sw / 2 - 0.6)} fill={GLASS} />
          <path d={glass(len - 1.6, len - 1.1, sw / 2 - 0.7, sw / 2 - 0.55)} fill={GLASS} />
          {/* twin stripes */}
          <path d={`M-0.45 0.3 V2.2 M0.45 0.3 V2.2 M-0.45 3.5 V${len - 0.3} M0.45 3.5 V${len - 0.3}`}
            stroke="rgba(255,255,255,0.55)" strokeWidth={0.3} />
        </>
      );
    }
    default: // sedan
      return (
        <>
          {body(1.2)}
          <path d={glass(1.8, 2.7, hw - 0.4, hw - 0.6)} fill={GLASS} />
          <path d={glass(len - 1.8, len - 1.2, hw - 0.6, hw - 0.4)} fill={GLASS} />
        </>
      );
  }
}

function Ambulance({ len, w }) {
  const hw = w / 2;
  const c = len / 2 + 0.6;
  return (
    <>
      <rect x={-hw} y={0} width={w} height={len} rx={0.7} fill="#ffffff" stroke="#9b1c1c" strokeWidth={0.45} />
      <path d={glass(0.9, 1.7, hw - 0.35, hw - 0.45)} fill={GLASS} />
      {/* light bar: red and blue */}
      <rect x={-hw + 0.4} y={2.0} width={hw - 0.4} height={0.55} fill="#e02424" />
      <rect x={0} y={2.0} width={hw - 0.4} height={0.55} fill="#2563eb" />
      {/* red cross on the roof */}
      <path d={`M0 ${c - 1.4} V${c + 1.4} M-1.4 ${c} H1.4`} stroke="#d03b3b" strokeWidth={0.9} />
    </>
  );
}

function Motorcycle({ len, w, paint }) {
  const hw = w / 2;
  return (
    <>
      <ellipse cx={0} cy={len / 2} rx={hw} ry={len / 2} fill={paint} stroke={OUTLINE} strokeWidth={0.35} />
      {/* handlebar and rider's helmet */}
      <path d={`M${-hw - 0.5} ${len * 0.22} H${hw + 0.5}`} stroke={OUTLINE} strokeWidth={0.45} strokeLinecap="round" />
      <circle cx={0} cy={len * 0.45} r={hw * 0.75} fill={GLASS} />
    </>
  );
}

function Bus({ len, w, paint }) {
  const hw = w / 2;
  const windows = [];
  for (let y = 2.4; y < len - 1.2; y += 1.6) {
    windows.push(<rect key={y} x={-hw + 0.25} y={y} width={0.45} height={1.1} fill={GLASS} />,
      <rect key={`r${y}`} x={hw - 0.7} y={y} width={0.45} height={1.1} fill={GLASS} />);
  }
  return (
    <>
      <rect x={-hw} y={0} width={w} height={len} rx={0.6} fill={paint} stroke={OUTLINE} strokeWidth={0.45} />
      <path d={glass(0.5, 1.6, hw - 0.3, hw - 0.3)} fill={GLASS} />
      {windows}
      {/* roof hatches */}
      <rect x={-0.9} y={len * 0.35} width={1.8} height={1.2} fill="rgba(0,0,0,0.18)" />
      <rect x={-0.9} y={len * 0.65} width={1.8} height={1.2} fill="rgba(0,0,0,0.18)" />
    </>
  );
}

function Truck({ len, w, paint }) {
  const hw = w / 2;
  const cab = Math.min(2.6, len * 0.26);
  return (
    <>
      {/* cab */}
      <rect x={-hw} y={0} width={w} height={cab} rx={0.5} fill={paint} stroke={OUTLINE} strokeWidth={0.45} />
      <path d={glass(0.35, 1.1, hw - 0.3, hw - 0.3)} fill={GLASS} />
      {/* cargo box */}
      <rect x={-hw} y={cab + 0.35} width={w} height={len - cab - 0.35} rx={0.25} fill="#d7d9dc" stroke={OUTLINE}
        strokeWidth={0.45} />
      <path d={`M${-hw + 0.4} ${cab + 1.2} H${hw - 0.4} M${-hw + 0.4} ${len - 0.9} H${hw - 0.4}`} stroke="rgba(0,0,0,0.22)" strokeWidth={0.3} />
    </>
  );
}

/** Roof dot: the vehicle has a VANET radio (connected). Vehicles without a radio have no dot. */
const RADIO = new Set(['equipped', 'nav', 'eco', 'driven', 'emergency']);

/**
 * One vehicle, drawn pointing up from its front bumper.
 * kind: what it can do (from its flags: driven, emergency, eco, nav, equipped, plain);
 * category: what it is (car, eco_car, motorcycle, bus, truck, emergency).
 * Every eco vehicle has the same body colour (--eco); other cars a stable paint picked from the id.
 */
export function CarBody({ id, kind, category = 'car', len, w }) {
  if (kind === 'emergency' || category === 'emergency') return <Ambulance len={len} w={w} />;
  const look = carLook(id);
  const eco = category === 'eco_car' || kind === 'eco';
  const paint = kind === 'driven' ? 'var(--driver)' : eco ? 'var(--eco)' : category === 'bus' ? '#f2b418' : look.paint;
  let body;
  if (category === 'motorcycle') body = <Motorcycle len={len} w={w} paint={paint} />;
  else if (category === 'bus') body = <Bus len={len} w={w} paint={paint} />;
  else if (category === 'truck') body = <Truck len={len} w={w} paint={paint} />;
  else body = <Body style={kind === 'driven' ? 'sedan' : eco ? 'hatchback' : look.style} paint={paint} len={len} w={w} />;
  return (
    <>
      {kind === 'driven' && (
        <rect x={-w / 2 - 0.6} y={-0.6} width={w + 1.2} height={len + 1.2} rx={1.6} fill="none"
          stroke="var(--driver)" strokeWidth={0.7} />
      )}
      {body}
      {RADIO.has(kind) && kind !== 'driven' && (
        <circle cx={0} cy={len * 0.55} r={Math.min(0.6, w / 4)} fill="var(--series-1)" stroke="#ffffff" strokeWidth={0.25} />
      )}
    </>
  );
}

/** Small legend icon: a car drawn pointing up. */
export function CarIcon({ kind, category, id = 'legend', style, paint, len = 6.5, w = 4.2, size = 22 }) {
  const pad = 0.8;
  return (
    <svg width={size * (w + 2 * pad) / (len + 2 * pad)} height={size}
      viewBox={`${-w / 2 - pad} ${-pad} ${w + 2 * pad} ${len + 2 * pad}`} aria-hidden="true">
      {style ? <Body style={style} paint={paint} len={len} w={w} />
        : <CarBody id={id} kind={kind} category={category} len={len} w={w} />}
    </svg>
  );
}
