/**
 * Roads offered as start and destination (driver mode, ambulance). Research networks: entry roads
 * (from a non-signal node into a signal) and exit roads (out of one). City network: every road, since
 * trips start and end anywhere on the ring and the streets. Labels name the road type there.
 */
export function roadChoices(network) {
  if (!network) return { starts: [], ends: [], byId: {} };
  const byId = Object.fromEntries(network.edges.map((e) => [e.id, e]));
  const city = network.edges.some((e) => e.type && e.type !== 'grid');
  if (city) {
    const label = (e) => `${e.from} → ${e.to}${e.type && e.type !== 'grid' ? ` (${e.type.replace('_', ' ')})` : ''}`;
    const all = network.edges.map((e) => ({ id: e.id, label: label(e) })).sort((a, b) => a.label.localeCompare(b.label));
    return { starts: all, ends: all, byId };
  }
  const tls = new Set(network.junctions.filter((j) => j.tls).map((j) => j.id));
  const starts = network.edges.filter((e) => !tls.has(e.from) && tls.has(e.to))
    .map((e) => ({ id: e.id, label: `${e.from} → ${e.to}` }));
  const ends = network.edges.filter((e) => tls.has(e.from) && !tls.has(e.to))
    .map((e) => ({ id: e.id, label: `${e.from} → ${e.to}` }));
  return { starts, ends, byId };
}
