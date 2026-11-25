// Dynamic congestion-aware controller (sim/controllers/dynamic.py): what it measures on each road,
// the priority scores, the green it chose and why. Shown inside a junction card.
import { fmt } from '../lib/format.js';

const DIRS = ['N', 'E', 'S', 'W'];
const FROM = { N: 'North', S: 'South', E: 'East', W: 'West' };
const GROUP = { NS: 'North + South', EW: 'East + West', N: 'North', S: 'South', E: 'East', W: 'West' };
const SIG_WORD = { G: 'Green', y: 'Yellow', r: 'Red' };
const DOWN = {
  clear: ['Clear', 'good'], busy: ['Busy', ''], blocked: ['Blocked', 'bad'], closed: ['Road closed', 'bad'],
};
const TIER = { short: 'short (light traffic)', medium: 'medium (moderate traffic)', long: 'long (heavy traffic)' };

// Junction panel: which road has priority, its green time, each road's measures, and the reason.
export default function DynamicDecision({ info, id }) {
  if (!info) return null;
  const serving = info.serving;
  const roads = info.roads || {};
  const ns = info.group_scores?.NS ?? 0;
  const ew = info.group_scores?.EW ?? 0;
  const log = (info.log || []).slice(-5).reverse();
  return (
    <div className="stack dyn" aria-label={`Signal decisions at ${id}`}>
      <div className="row" style={{ alignItems: 'center' }}>
        {serving ? (
          <span className="chip good">Priority: {GROUP[serving]}</span>
        ) : (
          <span className="chip">{info.transition === 'ALL_RED' ? 'All red' : 'Yellow'}{info.next ? `, then ${GROUP[info.next]}` : ''}</span>
        )}
        {serving && info.locked_s != null && <span className="chip">Change announced: {fmt(info.locked_s, 0)} s</span>}
        {serving && info.planned_s != null && (
          <span className="small">
            Green <strong className="num">{fmt(info.planned_s, 0)} s</strong> {TIER[info.tier] ? `· ${TIER[info.tier]}` : ''}
            {' · '}<span className="num">{fmt(info.elapsed_s, 0)} s</span> used
            {info.remaining_s > 0
              ? <>, <strong className="num">{fmt(info.remaining_s, 0)} s</strong> left</>
              : ' · held: nobody is waiting on red'}
          </span>
        )}
      </div>
      {info.mode === 'level' ? (
        <p className="small muted" style={{ margin: 0 }}>
          The road with the most cars goes first, {serving ? <>down to <strong className="num">{fmt(info.target_queue, 0)}</strong> cars (the next longest queue)</> : 'down to the next longest queue'}.
          Level roads then take turns, and the cars left are freed. Green {fmt(info.params?.min_green, 0)}–{fmt(info.params?.max_green, 0)} s; yellow {fmt(info.params?.yellow, 0)} s (from the road's speed);
          {info.params?.dynamic_clearance
            ? <> all red {fmt(info.params?.all_red_min, 0)}–{fmt(info.params?.all_red_max, 0)} s, until the junction is empty{info.params?.last_all_red != null ? ` (last: ${fmt(info.params.last_all_red, 0)} s)` : ''}.</>
            : <> all red {fmt(info.params?.all_red, 0)} s.</>}
        </p>
      ) : (
      <p className="small muted" style={{ margin: 0 }}>
        Priority score North–South <strong className="num">{fmt(ns, 1)}</strong> · East–West <strong className="num">{fmt(ew, 1)}</strong>.
        Green length between {fmt(info.params?.min_green, 0)} and {fmt(info.params?.max_green, 0)} s from the queue; yellow {fmt(info.params?.yellow, 0)} s, all red {fmt(info.params?.all_red, 0)} s.
      </p>
      )}
      <div className="table-wrap" tabIndex={0} role="region" aria-label={`Road measures at ${id} (scrolls sideways on small screens)`}>
        <table className="data dyn-table">
          <thead>
            <tr>
              <th>Road</th><th>Signal</th><th>Waiting</th><th>Queue</th><th>Density</th><th>Avg wait</th>
              <th>Arriving</th><th>Downstream</th><th>{info.mode === 'level' ? 'Rank value' : 'Score'}</th>
            </tr>
          </thead>
          <tbody>
            {DIRS.filter((d) => roads[d]).map((d) => {
              const r = roads[d];
              const [downText, downCls] = DOWN[r.down_status] || [r.down_status, ''];
              const prio = Boolean(serving?.includes(d));
              return (
                <tr key={d} className={prio ? 'selected' : ''}>
                  <td>
                    <strong>{FROM[d]}</strong> <span className="small muted">{(r.roads || []).join(', ')}</span>
                    {prio && <> <span className="chip good">priority</span></>}
                  </td>
                  <td><span className={`sig sig-${r.signal}`} aria-hidden="true">{(r.signal || '?').toUpperCase()}</span> <span className="small">{SIG_WORD[r.signal] || ''}</span></td>
                  <td className="num">{fmt(r.waiting, 0)}</td>
                  <td className="num">{fmt(r.queue_m, 0)} m</td>
                  <td className="num">{fmt(r.density, 0)} <span className="small muted">veh/km</span></td>
                  <td className="num">{fmt(r.mean_wait_s, 0)} s</td>
                  <td className="num">{fmt(r.flow_vpm, 1)} <span className="small muted">/min</span></td>
                  <td>
                    <span className={`chip ${downCls}`} title={`${Math.round((r.down_occ || 0) * 100)} % full`}>{downText}</span>
                    <span className="small muted"> {Math.round((r.down_occ || 0) * 100)} %{r.down_worst ? ` · ${r.down_worst}` : ''}</span>
                  </td>
                  <td className="num"><strong>{fmt(r.score, 1)}</strong></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {info.reason && <div className="notice" role="status"><strong>Decision:</strong> {info.reason}</div>}
      {log.length > 1 && (
        <details>
          <summary className="small">Recent decisions</summary>
          <ul className="plain-list small">
            {log.map((e, i) => <li key={`${e.t}-${i}`}><span className="num muted">t = {e.t} s</span> · {e.text}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}
