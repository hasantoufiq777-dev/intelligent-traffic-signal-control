/** Entry roads (start at a non-signal node) and exit roads (end at one), from the road map. */
export function roadChoices(network) {
  if (!network) return { starts: [], ends: [], byId: {} };
  const tls = new Set(network.junctions.filter((j) => j.tls).map((j) => j.id));
  const starts = network.edges.filter((e) => !tls.has(e.from) && tls.has(e.to))
    .map((e) => ({ id: e.id, label: `${e.from} → ${e.to}` }));
  const ends = network.edges.filter((e) => tls.has(e.from) && !tls.has(e.to))
    .map((e) => ({ id: e.id, label: `${e.from} → ${e.to}` }));
  return { starts, ends, byId: Object.fromEntries(network.edges.map((e) => [e.id, e])) };
}
