// Vehicle flags (bit field set by the simulation) and labels.
export const FLAG = { ECO: 1, EMERGENCY: 2, NAV: 4, ADVISED: 8, EQUIPPED: 16, DRIVER: 32 };

export const has = (flags, bit) => (Number(flags) & bit) !== 0;

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

export const APPROACH_LABEL = { N: 'from the north', S: 'from the south', E: 'from the east', W: 'from the west' };
