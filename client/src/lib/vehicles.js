// Vehicle flags (bit field set by the simulation), categories and labels.
export const FLAG = { ECO: 1, EMERGENCY: 2, NAV: 4, ADVISED: 8, EQUIPPED: 16, DRIVER: 32 };

export const has = (flags, bit) => (Number(flags) & bit) !== 0;

/** What a vehicle can do (from its flags): driven, ambulance, eco, navigating, connected or plain. */
export function vehicleKind(flags) {
  if (has(flags, FLAG.DRIVER)) return 'driven';
  if (has(flags, FLAG.EMERGENCY)) return 'emergency';
  if (has(flags, FLAG.ECO)) return 'eco';
  if (has(flags, FLAG.NAV)) return 'nav';
  if (has(flags, FLAG.EQUIPPED)) return 'equipped';
  return 'plain';
}

export const KIND_LABEL = {
  driven: 'Driven by a viewer',
  emergency: 'Ambulance',
  eco: 'Eco car (follows advice)',
  nav: 'Navigating car',
  equipped: 'Connected car',
  plain: 'Car without radio',
};

// ---- vehicle categories (sim/vehicles.py): size and type come from SUMO, not from the browser
/** The one colour of every eco vehicle, everywhere (map, driver view, details, legend). */
export const ECO_COLOR = '#16a34a';

export const CATEGORY_LABEL = {
  car: 'Car', eco_car: 'Eco car', motorcycle: 'Motorcycle', bus: 'Bus', truck: 'Truck', emergency: 'Ambulance',
};

/** Real sizes (m) when the road map has no catalogue (research networks: cars 5 m, ambulance 6.5 m). */
const DEFAULT_SIZE = {
  car: [5.0, 1.8, 1.5], eco_car: [4.3, 1.8, 1.5], motorcycle: [2.2, 0.9, 1.4], bus: [12.0, 2.55, 3.2],
  truck: [10.0, 2.5, 3.6], emergency: [6.5, 2.1, 2.6],
};

/**
 * Category used for drawing: the row's category (city runs) or, on the research networks, from the
 * flags. Any vehicle flagged eco is drawn as an eco vehicle (one colour), whatever its category.
 */
export function categoryOf(kindField, flags) {
  if (has(flags, FLAG.EMERGENCY)) return 'emergency';
  if (kindField === 'eco_car' || (has(flags, FLAG.ECO) && (!kindField || kindField === 'car'))) return 'eco_car';
  if (kindField && CATEGORY_LABEL[kindField]) return kindField;
  return 'car';
}

/** Length, width, height (m) of a category, from the network's vehicle_types when present. */
export function sizeOf(category, vehicleTypes) {
  const t = vehicleTypes?.find?.((v) => v.id === category);
  if (t) return [t.length, t.width, t.height ?? DEFAULT_SIZE[category]?.[2] ?? 1.5];
  return DEFAULT_SIZE[category] ?? DEFAULT_SIZE.car;
}

/** Index of a named field in a vehicles message (older messages have no kind / lane / limit). */
export function fieldIndex(vehicles, name) {
  const i = vehicles?.fields?.indexOf?.(name);
  return i == null ? -1 : i;
}

export const APPROACH_LABEL = { N: 'from the north', S: 'from the south', E: 'from the east', W: 'from the west' };

/**
 * Heading to draw, as the turn-the-short-way equivalent of the previous one. SUMO headings wrap at
 * 0/360, and the CSS transition between frames rotates by the plain difference, so 10 -> 280 deg (a
 * left turn) would spin 270 deg clockwise. Keeping a continuous angle per car makes it turn -90 deg.
 */
export function continuousAngle(prev, angle) {
  if (prev === undefined) return angle;
  const delta = ((((angle - prev) % 360) + 540) % 360) - 180; // in [-180, 180)
  return prev + delta;
}
