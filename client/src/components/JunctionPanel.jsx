// Detail of one signalised junction: phase, signal and countdown per approach, queues (true and
// as estimated over the VANET), the controller in use and emergency preemption.
import { fmt } from '../lib/format.js';

const DIRS = ['N', 'E', 'S', 'W'];
const FROM = { N: 'North', S: 'South', E: 'East', W: 'West' };
const SIG_WORD = { G: 'Green', y: 'Yellow', r: 'Red' };
const PHASE_TEXT = {
  NS_GREEN: 'North–south green', EW_GREEN: 'East–west green', NS_YELLOW: 'North–south yellow', EW_YELLOW: 'East–west yellow',
  ALL_RED: 'All red (clearing the junction)', EV_GREEN: 'Green for the ambulance only', EV_TRANSITION: 'Switching for the ambulance',
  EV_RECOVERY: 'Returning to normal control',
};
const CONTROLLER_TEXT = {
  fixed: 'Fixed-time (30 s per direction)', adaptive: 'Adaptive, exact queues', adaptive_vanet: 'Adaptive, queues over VANET',
  adaptive_ev: 'Adaptive + ambulance preemption (exact)', adaptive_ev_vanet: 'Adaptive + preemption over VANET',
  adaptive_ev_vanet_tuned: 'Adaptive + preemption over VANET, GA-tuned',
};

/** "12 s" when the change time is certain, "8–30 s" when the controller gives a window, else null. */
function changeIn(sp) {
  if (!sp || sp.min_s == null) return null;
  const a = Math.ceil(sp.min_s);
  if (sp.max_s == null) return `at least ${a} s`;
  const b = Math.ceil(sp.max_s);
  return a === b ? `${a} s` : `${a}–${b} s`;
}

export default function JunctionPanel({ ids, selected, onSelect, tlsState, ev, controller }) {
  const id = selected && tlsState?.[selected] ? selected : null;
  const data = id ? tlsState[id] : null;
  const evHere = (ev || []).filter((e) => e.tls === id);
  const maxQ = Math.max(8, ...(data ? DIRS.map((d) => data.queues?.[d] ?? 0) : [0]));
  const preempting = data && /^EV/.test(data.phase || '');
  return (
    <section className="card stack" aria-labelledby="jp-h">
      <div className="card-head">
        <h2 id="jp-h">Junction{ id ? ` ${id}` : '' }</h2>
        <div className="seg" role="group" aria-label="Choose a junction">
          {ids.map((j) => (
            <button key={j} type="button" className={`seg-btn${j === id ? ' active' : ''}`} aria-pressed={j === id}
              onClick={() => onSelect(j === id ? null : j)}>{j}</button>
          ))}
        </div>
      </div>
      {!ids.length && <p className="muted" style={{ margin: 0 }}>No signal data yet.</p>}
      {ids.length > 0 && !data && <p className="muted" style={{ margin: 0 }}>Select a junction on the map or above to see its signals and queues.</p>}
      {data && (
        <>
          <div className="row">
            <span className={`chip${preempting ? ' bad' : ''}`}>{PHASE_TEXT[data.phase] || data.phase}</span>
            {controller && <span className="small muted">Controller: {CONTROLLER_TEXT[controller] || controller}</span>}
          </div>
          <div className="table-wrap">
            <table className="data approach-table">
              <thead><tr><th>From</th><th>Signal</th><th>Changes in</th><th>Waiting</th><th>VANET estimate</th></tr></thead>
              <tbody>
                {DIRS.map((d) => {
                  const s = data.signal?.[d];
                  const q = data.queues?.[d] ?? 0;
                  const est = data.queues_est?.[d];
                  return (
                    <tr key={d}>
                      <td>{FROM[d]}</td>
                      <td><span className={`sig sig-${s || 'none'}`} aria-hidden="true">{s ? s.toUpperCase() : '?'}</span> {SIG_WORD[s] || 'Unknown'}</td>
                      <td className="num">{changeIn(data.spat?.[d]) ?? '–'}</td>
                      <td className="num">
                        <span className="qcell"><span className="qbar" style={{ width: `${Math.min(100, (q / maxQ) * 100)}%` }} /></span> {q}
                      </td>
                      <td className="num">{est ?? '–'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {data.spat?.N?.preempted && <p className="small muted" style={{ margin: 0 }}>Timing is not predictable while an ambulance has priority.</p>}
          {evHere.map((e) => (
            <div className="drive-alert" key={e.id} role="status">
              Ambulance {e.id} approaching from the {FROM[e.approach]?.toLowerCase() ?? '?'}: ETA {fmt(e.eta)} s, {fmt(e.dist, 0)} m
              {e.alert_age != null && ` · alert ${fmt(e.alert_age, 1)} s old`}
            </div>
          ))}
        </>
      )}
    </section>
  );
}
