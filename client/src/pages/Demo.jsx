// Guided demonstration for a teacher or evaluator: thirteen steps through the project. A step
// with a scenario starts it on the shared simulation (administrators and researchers) and says
// what to watch for and when; the live map shows the run that is actually playing.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api.js';
import NowPlaying from '../components/NowPlaying.jsx';
import TrafficMap from '../components/TrafficMap.jsx';
import { useAuth } from '../hooks/useAuth.js';
import { useLive } from '../hooks/useLiveSocket.js';

const STEP_KEY = 'itsc-demo-step';

export const STEPS = [
  { title: 'Project overview', page: ['/docs', 'Open the documentation'],
    text: 'Traffic lights, ambulance priority, navigation and speed advice are controlled with information cars exchange over a vehicular radio network (VANET). SUMO simulates the traffic; this site shows it live.',
    watch: ['The documentation explains the problem, the architecture and each algorithm with its formula.'] },
  { title: 'The road network', page: ['/', 'Open the live map'],
    text: 'Four signalised junctions (J0–J3) on a 2×2 grid, 250 m apart, two lanes each way. Each junction has a roadside unit that listens to the cars.',
    watch: ['On the map, open Layers and switch on “VANET radio range” to see the 300 m range of each roadside unit.', 'Select a junction to see its signals and queues.'] },
  { title: 'Normal traffic, fixed-time signals', entry: { scenario: 'normal', network: 'grid2x2', controller: 'fixed', nav: 'none', seed: 1 },
    text: 'The baseline: every direction gets 30 s of green whether or not anyone is waiting.',
    watch: ['Signals count down from 30 s.', 'Cars wait at red even when the cross road is empty.'] },
  { title: 'Congested traffic', entry: { scenario: 'congested', network: 'grid2x2', controller: 'fixed', nav: 'none', seed: 1 },
    text: 'The same fixed-time signals with heavy traffic.',
    watch: ['Queue counts at the junctions grow; roads turn amber and red on the map.', 'The Congestion tile rises to Moderate or Heavy.'] },
  { title: 'Adaptive signal control', entry: { scenario: 'congested', network: 'grid2x2', controller: 'adaptive_vanet', nav: 'none', seed: 1 }, page: ['/signals', 'Open Signals'],
    text: 'The controller compares the queues of the two directions every 5 s and gives green where it is needed (10–50 s, no approach waits more than 60 s).',
    watch: ['Greens now have different lengths.', 'Queues are shorter than in the previous step (evaluated: waiting −41 % when congested).'] },
  { title: 'VANET monitoring', entry: { scenario: 'normal', network: 'grid2x2', controller: 'adaptive_vanet', nav: 'none', seed: 1, penetration: 0.5, loss: 0.1 }, page: ['/vanet', 'Open VANET'],
    text: 'Only half the cars have a radio and at least 10 % of the messages are lost. The roadside units still estimate the queues.',
    watch: ['Delivery ratio, loss and delay.', 'The roadside-unit table compares the estimated queue with the true one.'] },
  { title: 'Ambulance preemption', entry: { scenario: 'emergency', network: 'grid2x2', controller: 'adaptive_ev_vanet', nav: 'none', seed: 2, loss: 0.1, relay: 'on' }, page: ['/emergency', 'Open Emergency'],
    text: 'An ambulance enters after 120 simulated seconds (about 30 s here). Its alerts reach the junctions over the radio network.',
    watch: ['Its route is drawn on the map; each junction ahead shows AMBULANCE and turns green for it.', 'Events list “Preemption started”.'] },
  { title: 'Accident detection', entry: { scenario: 'incident', controller: 'adaptive_ev_vanet', nav: 'astar', seed: 1 }, page: ['/incidents', 'Open Incidents'],
    text: 'At 150 s a crash closes the road J0→J1. Nobody tells the system: a roadside unit notices cars that have been stopped for 30 s.',
    watch: ['The road is marked “Road closed · not yet detected”, then detected at about 183 s.', 'After that, navigating cars avoid the road.'] },
  { title: 'Road closure and the ambulance', entry: { scenario: 'ev_incident', controller: 'adaptive_ev_vanet', nav: 'none', seed: 1, ev_routing: 'on' }, page: ['/', 'Open the live map'],
    text: 'A closure lies on the ambulance’s route. With route choice on, it plans around it on the traffic centre’s map.',
    watch: ['Road J1→J3 closes at 150 s.', 'At about 242 s the ambulance’s route (pink) changes to go around; the junctions on the new route give it green.'] },
  { title: 'Contraflow at an accident', entry: { scenario: 'accident_zone', controller: 'adaptive_ev_vanet', nav: 'none', seed: 1 }, page: ['/work-zone', 'Open Work zone'],
    text: 'A crash blocks the westbound side of the J2–J3 road. Both directions share one open lane, one at a time, with automatic signals.',
    watch: ['From about 183 s: the shared lane with arrows for the current direction; both ends red while it clears.', 'At about 300 s an ambulance gets the next turn.', 'At about 530 s the road returns to normal.'] },
  { title: 'Navigation', entry: { scenario: 'navigation', controller: 'adaptive_vanet', nav: 'aco', seed: 3 }, page: ['/navigation', 'Open Navigation'],
    text: 'Navigating cars choose routes from the live speed map. Ant colony optimisation spreads them over alternative roads.',
    watch: ['Trip time of navigating cars against the others.', 'Route changes are listed as events.'] },
  { title: 'Genetic algorithm and ACO tuning', page: ['/ga', 'Open GA optimisation'],
    text: 'A genetic algorithm tuned the ACO and signal parameters offline and was checked on seeds it had never seen.',
    watch: ['The convergence curve.', 'ACO fitness improved by 35 % on held-out seeds; the signal parameters by about 4 %, which is not significant.'] },
  { title: 'Research results', page: ['/results', 'Open Results'],
    text: 'All comparisons over 10 seeds with statistical tests: signals, ambulance, navigation, eco-driving, ambulance routing and the work zone.',
    watch: ['Each table gives the mean, the spread, the change against the baseline and the p-value.', 'Results can be downloaded as CSV.'] },
];

export default function Demo() {
  const live = useLive();
  const { canControl } = useAuth();
  const [step, setStepState] = useState(() => {
    try {
      return Math.min(STEPS.length - 1, Math.max(0, Number(localStorage.getItem(STEP_KEY)) || 0));
    } catch {
      return 0;
    }
  });
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const setStep = (n) => {
    setMsg(null);
    setStepState(n);
    try {
      localStorage.setItem(STEP_KEY, String(n));
    } catch {
      /* per-viewer convenience only */
    }
  };
  const s = STEPS[step];
  const np = live.nowPlaying;
  const cur = np?.current?.entry;
  const playing = Boolean(s.entry && np?.running && cur
    && Object.entries(s.entry).every(([k, v]) => k === 'network' || cur[k] === v));

  async function start() {
    setBusy(true);
    setMsg(null);
    try {
      await api.start({ ...s.entry, label: `Demo ${step + 1}: ${s.title}` });
      setMsg({ text: 'Started. The simulation runs four times faster than real time.' });
    } catch (err) {
      setMsg({ error: true, text: err.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <NowPlaying nowPlaying={np} latest={live.latest} viewers={live.viewers} status={live.status} />
      <div className="demo-layout">
        <nav className="card demo-steps" aria-label="Demonstration steps">
          <ol>
            {STEPS.map((x, i) => (
              <li key={x.title}>
                <button type="button" className={`demo-step${i === step ? ' active' : ''}`} aria-current={i === step ? 'step' : undefined} onClick={() => setStep(i)}>
                  <span className="demo-n">{i + 1}</span>{x.title}
                </button>
              </li>
            ))}
          </ol>
        </nav>
        <div className="stack">
          <section className="card stack" aria-labelledby="demo-h">
            <div className="card-head"><h2 id="demo-h">Step {step + 1} of {STEPS.length}: {s.title}</h2>
              {s.entry && <span className={`chip${playing ? '' : ' muted'}`}>{playing ? 'this scenario is playing' : 'scenario not playing'}</span>}</div>
            <p style={{ margin: 0 }}>{s.text}</p>
            <div>
              <h3>What to look for</h3>
              <ul className="demo-watch">{s.watch.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>
            <div className="row">
              {s.entry && canControl && (
                <button type="button" className="btn primary" disabled={busy || np?.lifecycle === 'STARTING' || np?.lifecycle === 'STOPPING'} onClick={start}>
                  {playing ? 'Restart this scenario' : 'Start this scenario'}
                </button>
              )}
              {s.page && <Link className="btn" to={s.page[0]}>{s.page[1]}</Link>}
              <span style={{ marginLeft: 'auto' }} className="row">
                <button type="button" className="btn" disabled={step === 0} onClick={() => setStep(step - 1)}>Previous</button>
                <button type="button" className="btn" disabled={step === STEPS.length - 1} onClick={() => setStep(step + 1)}>Next</button>
              </span>
            </div>
            {s.entry && !canControl && (
              <p className="small muted" style={{ margin: 0 }}>
                Starting a scenario needs a researcher or administrator account. You can still follow the run that is playing
                {playing ? '.' : '; this step’s scenario also comes up in the playlist.'}
              </p>
            )}
            {s.entry && (
              <p className="small muted" style={{ margin: 0 }}>
                Scenario: {s.entry.scenario} · controller: {s.entry.controller}{s.entry.nav !== 'none' ? ` · navigation: ${s.entry.nav}` : ''}
                {s.entry.penetration != null ? ` · ${Math.round(s.entry.penetration * 100)} % of cars connected` : ''}
                {s.entry.loss ? ` · base loss ${s.entry.loss}` : ''}{s.entry.ev_routing === 'on' ? ' · ambulance route choice on' : ''} · seed {s.entry.seed}.
                Starting it replaces the run everyone is watching.
              </p>
            )}
            {msg && <div className={`notice${msg.error ? ' error' : ''}`} role="status">{msg.text}</div>}
          </section>
          {s.entry && (
            <section className="card map-card" aria-label="Live map">
              <TrafficMap network={live.network} vehicles={live.vehicles} tlsState={live.latest?.tls} zoneState={live.latest?.zone}
                edgeSpeeds={live.latest?.edge_speeds} incidents={live.latest?.incidents} evRoutes={live.latest?.ev_routes}
                followId={live.followId} onSelect={live.follow}
                stale={!np?.running ? 'The simulation is not running' : !playing ? 'Another scenario is playing: start this step’s scenario to see it' : null} />
            </section>
          )}
        </div>
      </div>
    </>
  );
}
