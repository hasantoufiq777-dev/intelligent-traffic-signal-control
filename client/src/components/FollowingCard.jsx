// Car following: the vehicle ahead (speed, acceleration, gap), the advice for my car from it, and
// the vehicle behind, which follows my car the same way (sim/car_following.py).
import { fmt, isNum, kmh, seconds } from '../lib/format.js';
import { accText, FOLLOW_TEXT, timeGap } from '../lib/following.js';
import { CATEGORY_LABEL } from '../lib/vehicles.js';

const STOPPING = new Set(['stopped', 'brake']); // the target is then the speed it brakes down from

const label = (o) => `${CATEGORY_LABEL[o.kind] ?? 'Vehicle'} ${o.id}`;

function Side({ title, o, gapLabel, timeGapS }) {
  return (
    <div className="follow-side">
      <div className="k">{title}</div>
      {o ? (
        <>
          <div className="v">{label(o)}</div>
          <div className="small"><span className="num">{fmt(kmh(o.speed), 0)}</span> km/h · {accText(o.acc)}</div>
          <div className="small muted">
            {gapLabel} <span className="num">{fmt(o.gap, 0)} m</span>
            {isNum(timeGapS) ? <> (<span className="num">{fmt(timeGapS, 1)} s</span>)</> : null}
            {' · '}{o.v2v ? 'shares speed and braking by V2V' : 'no radio: measured by radar'}
          </div>
        </>
      ) : <div className="small muted">None within 100 m</div>}
    </div>
  );
}

/**
 * data: normalise() result. mine: the viewer drives this car (the advice is theirs to follow).
 * onFollow: the "Follow the car ahead" action (driver only); active: follow mode is on.
 */
export default function FollowingCard({ data, mine = false, onFollow, active = false, onSelect }) {
  if (!data) return null;
  const { ahead, behind, advice } = data;
  const t = FOLLOW_TEXT[advice?.state] ?? null;
  const target = advice && advice.state !== 'free' && isNum(advice.speed) ? kmh(advice.speed) : null;
  return (
    <div className="stack follow-card" aria-label="Car following">
      <div className="follow-grid">
        <Side title="Car ahead" o={ahead} gapLabel="gap" timeGapS={timeGap(ahead?.gap, data.speed)} />
        <Side title="Car behind" o={behind} gapLabel="behind you by" timeGapS={timeGap(behind?.gap, behind?.speed)} />
      </div>
      {t && (
        <div className={`advice advice-${t.cls}`}>
          <span className="small muted">
            {mine ? 'Following the car ahead' : 'This car follows the car ahead'}
            {ahead ? (ahead.v2v ? ' (cooperative: its braking arrives by V2V)' : ' (radar: speed and gap only)') : ''}
          </span>
          <span className="headline">{t.head}</span>
          {target != null && (
            <span className="small">
              {STOPPING.has(advice.state) ? 'Highest speed now to stop gently: ' : 'Speed for a safe gap: '}
              <strong className="num">{target} km/h</strong>
              {isNum(advice.ttc) && advice.ttc < 10 ? <> · time to collision <span className="num">{seconds(advice.ttc)}</span></> : null}
            </span>
          )}
          {onFollow && (
            <button type="button" className={`btn${active ? ' primary' : ''}`} style={{ alignSelf: 'flex-start' }}
              onClick={onFollow} aria-pressed={active} aria-keyshortcuts="F">
              {active ? 'Following the car ahead' : 'Follow the car ahead'} <kbd>F</kbd>
            </button>
          )}
        </div>
      )}
      {behind && (
        <p className="small muted" style={{ margin: 0 }}>
          {behind.v2v ? <>The car behind gets {mine ? 'your' : 'this car’s'} speed and acceleration by V2V and</>
            : <>The car behind measures {mine ? 'your' : 'this car’s'} speed and the gap with its radar and</>}
          {' '}{FOLLOW_TEXT[behind.follow]?.behind ?? 'follows'}
          {isNum(behind.followSpeed) && !STOPPING.has(behind.follow) ? <> (target <span className="num">{kmh(behind.followSpeed)} km/h</span>)</> : null}.
        </p>
      )}
      {onSelect && (ahead || behind) && (
        <div className="row">
          {ahead && <button type="button" className="btn" onClick={() => onSelect(ahead.id)}>Ride with the car ahead</button>}
          {behind && <button type="button" className="btn" onClick={() => onSelect(behind.id)}>Ride with the car behind</button>}
        </div>
      )}
    </div>
  );
}
