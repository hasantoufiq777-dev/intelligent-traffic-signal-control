// Accident work zone status (state message "zone", scenario accident_zone): which direction may use
// the shared lane, the queue on each side, ambulance priority and the safety count.

const SIG_LETTER = { G: 'G', y: 'Y', r: 'R' };
const SIG_WORD = { G: 'green', y: 'yellow', r: 'red' };
const SIDE = { E: 'Eastbound (from J2)', W: 'Westbound (from J3)' };

function headline(z) {
  if (z.mode === 'closure') return 'Westbound road closed at J3. Westbound traffic detours over other roads.';
  if (z.mode !== 'alternating') {
    return z.accident
      ? 'Accident reported. Waiting for the VANET to confirm it.'
      : 'No accident. Both carriageways are open.';
  }
  if (z.phase === 'clear') return 'Clearing the shared lane: both sides red until it is empty.';
  const d = z.dir === 'E' ? 'Eastbound' : 'Westbound';
  return z.phase === 'yellow' ? `${d} turn ending.` : `${d} turn: one direction at a time on the open lane.`;
}

function Side({ d, z }) {
  const s = z.mode === 'alternating' ? z.signal?.[d] : null;
  return (
    <div className="tile">
      <div className="k">{SIDE[d]}</div>
      <div className="v num" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className={`sig sig-${s || 'none'}`} aria-label={`Signal ${SIG_WORD[s] || 'off'}`}>{SIG_LETTER[s] || '–'}</span>
        {z.queue?.[d] ?? 0} <span className="small muted">waiting</span>
      </div>
      <div className="sub num">
        {z.mode === 'alternating' && s !== 'G' && z.red_for_s?.[d] ? `red for ${z.red_for_s[d]} s · ` : ''}
        {z.in_zone?.[d] ?? 0} in the zone
      </div>
    </div>
  );
}

export default function ZonePanel({ zone }) {
  if (!zone) return null;
  const alt = zone.mode === 'alternating';
  return (
    <section className="card stack" aria-labelledby="zone-h">
      <div className="card-head">
        <h2 id="zone-h">Accident zone</h2>
        <span className="chip">{alt ? 'alternating one-way' : zone.mode === 'closure' ? 'road closed' : 'normal'}</span>
      </div>
      <p className="small" style={{ margin: 0 }}>{headline(zone)}</p>
      {alt && (
        <div className="tiles">
          <Side d="E" z={zone} />
          <Side d="W" z={zone} />
        </div>
      )}
      {zone.ev && (
        <p className="small" style={{ margin: 0 }}>
          🚑 Ambulance {zone.ev} is coming: its direction gets the next turn and keeps it until it has passed.
        </p>
      )}
      {(alt || zone.switches > 0) && (
        <p className="small muted" style={{ margin: 0 }}>
          {zone.switches} turns so far · safety: {zone.conflicts === 0
            ? 'never both directions in the shared lane'
            : `${zone.conflicts} s with both directions in the lane`}
        </p>
      )}
    </section>
  );
}
