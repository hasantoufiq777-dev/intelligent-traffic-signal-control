// Project documentation for readers who do not open the source code: problem, objectives,
// architecture, algorithms with their formulas, method, results, limitations and future work.
// The result figures are the evaluated ones (results/summary.md, 10 seeds); the Results page shows them in full.
import { Link } from 'react-router-dom';

const SECTIONS = [
  ['overview', 'Overview'], ['problem', 'Problem and objectives'], ['architecture', 'Architecture'], ['simulation', 'Simulation method'],
  ['signals', 'Signal control'], ['vanet', 'VANET'], ['emergency', 'Ambulance priority'], ['navigation', 'Navigation: A* and ACO'],
  ['incidents', 'Incidents and the work zone'], ['glosa', 'Eco-driving (GLOSA)'], ['ga', 'Genetic algorithm'], ['evaluation', 'Evaluation'],
  ['results', 'Results'], ['limits', 'Limitations'], ['future', 'Future work'], ['tech', 'Technologies'],
];

function Formula({ children, note }) {
  return <p className="formula"><code>{children}</code>{note && <span className="small muted"> {note}</span>}</p>;
}

function Box({ children, wide }) {
  return <div className={`arch-box${wide ? ' wide' : ''}`}>{children}</div>;
}

export default function Docs() {
  return (
    <div className="docs">
      <nav className="docs-toc card" aria-label="Contents">
        <h2>Contents</h2>
        <ol>{SECTIONS.map(([id, title]) => <li key={id}><a href={`#${id}`}>{title}</a></li>)}</ol>
      </nav>
      <article className="docs-body">
        <section id="overview" className="card">
          <h2>Overview</h2>
          <p>
            This platform simulates road traffic on a grid of signalised junctions and controls the traffic lights, ambulance
            priority, navigation and speed advice with information that cars exchange over a <strong>VANET</strong> (vehicular
            ad hoc network: radio between cars, V2V, and between cars and roadside units, V2I). Traffic is simulated by SUMO;
            the control logic, the radio network and the algorithms are written in Python; this website shows the simulation live.
          </p>
          <p>Start with the <Link to="/demo">guided demonstration</Link>, or watch the <Link to="/">live map</Link>.</p>
        </section>

        <section id="problem" className="card">
          <h2>Problem and objectives</h2>
          <p>
            Fixed-time traffic lights give green whether or not anyone is waiting, ambulances lose time at red lights, and drivers
            learn about a blocked road only when they reach it. Sensors in the road see only the vehicles next to them.
          </p>
          <ul>
            <li>Control signals from the real queues, and test whether queues estimated from car radio messages are good enough, also when only some cars have a radio and messages are lost.</li>
            <li>Give ambulances green lights in time, using alerts sent over the radio network.</li>
            <li>Detect crashes from stopped cars, reroute traffic, and keep a half-blocked road usable with alternating one-way traffic.</li>
            <li>Reduce stops, fuel use and CO₂ with speed advice, and tune parameters automatically.</li>
            <li>Measure every claim over several random seeds with statistical tests.</li>
          </ul>
        </section>

        <section id="architecture" className="card">
          <h2>Architecture</h2>
          <div className="arch" role="img" aria-label="Architecture: browsers connect over HTTPS and WebSocket to the Node.js gateway, which uses a database and controls one Python simulation that drives SUMO">
            <Box wide>Browsers (up to 1000 signed-in users) — React dashboard</Box>
            <div className="arch-arrow">HTTPS + WebSocket (Socket.io)</div>
            <Box wide>Node.js gateway — sign-in and roles, validation, rate limits, one shared live stream</Box>
            <div className="arch-split">
              <div><div className="arch-arrow">SQL</div><Box>SQLite — users, sessions, runs, results, events, audit log</Box></div>
              <div><div className="arch-arrow">JSON lines (stdin / stdout)</div><Box>Python simulation — controllers, VANET, navigation, GLOSA, incidents, work zone</Box>
                <div className="arch-arrow">TraCI</div><Box>SUMO — moves every vehicle</Box></div>
            </div>
          </div>
          <p>
            There is one simulation for everyone: the gateway starts it, reads one message burst per simulated second and sends
            it to all viewers. Only administrators and researchers can start or stop a run. Nothing is simulated in the browser.
          </p>
        </section>

        <section id="simulation" className="card">
          <h2>Simulation method</h2>
          <p>The network is a 2×2 grid: junctions J0–J3, 250 m apart, two lanes per direction, 50 km/h. SUMO moves the cars with its car-following and lane-changing models, so cars keep safe gaps, stop at red and start one after another at green. Each simulated second runs in this order:</p>
          <ol>
            <li>Incidents (place or remove a crash)</li><li>VANET messages</li><li>Signal controllers</li><li>Accident-zone controller</li>
            <li>Ambulance routing and navigation</li><li>Speed advice (GLOSA)</li><li>Driver commands</li><li>Live output and metrics</li><li>SUMO moves every vehicle one second</li>
          </ol>
          <p>Trip statistics (waiting time, travel time, throughput, fuel, CO₂) come from SUMO’s trip records at the end of a run.</p>
        </section>

        <section id="signals" className="card">
          <h2>Signal control</h2>
          <p>Every change is <strong>green → yellow 3 s → all red 2 s → green</strong>, and a state with conflicting greens is refused.</p>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
            <table className="data left">
              <thead><tr><th>Controller</th><th>Rule</th></tr></thead>
              <tbody>
                <tr><td>Fixed-time</td><td className="wrap">30 s green per direction, whatever the traffic (the baseline).</td></tr>
                <tr><td>Adaptive</td><td className="wrap">Every 5 s compare the north–south and east–west queues. Green lasts 10–50 s. Switch early when the other side has at least 5 more vehicles waiting, when the served side is empty, or when an approach has been red for 60 s.</td></tr>
                <tr><td>Adaptive over VANET</td><td className="wrap">The same rule, with queues estimated by the roadside unit from car beacons.</td></tr>
              </tbody>
            </table>
          </div>
          <Formula note="switch from north–south to east–west">switch if Q_EW − Q_NS ≥ 5, or Q_NS ≤ 1 and Q_EW &gt; 0, or red time ≥ 60 s; always 10 s ≤ green ≤ 50 s</Formula>
        </section>

        <section id="vanet" className="card">
          <h2>VANET</h2>
          <p>Each connected car sends a beacon (position, speed, lane) once a second. The radio channel is emulated:</p>
          <Formula note="for distance d ≤ range R = 300 m; beyond R nothing arrives">P(loss) = base + (max − base) · (d / R)²</Formula>
          <p>Delay is 10 ms plus up to 5 ms jitter. A roadside unit counts the cars slower than 1 m/s within 100 m per approach:</p>
          <Formula note="so cars without a radio are accounted for">queue estimate = stopped connected cars ÷ share of cars with a radio</Formula>
          <p>All roadside units are wired to a traffic management centre (TMC), which keeps a map of road speeds and incidents.</p>
        </section>

        <section id="emergency" className="card">
          <h2>Ambulance priority</h2>
          <p>
            An ambulance broadcasts an alert every 0.5 s. Roadside units receive it directly or relayed by other cars (up to 3
            hops) and pass it on by wire to the junctions on its route. A junction the ambulance will reach within 18 s switches to
            green for the ambulance’s approach only, holds it until the ambulance has passed (at most 90 s), then returns to normal
            control. With route choice on, the ambulance plans its route with A* on the TMC map and avoids closed or jammed roads.
          </p>
        </section>

        <section id="navigation" className="card">
          <h2>Navigation: A* and ACO</h2>
          <Formula note="per road; a closed road costs infinity">cost = length ÷ current speed + 8 s per traffic light</Formula>
          <p><strong>A*</strong> finds the single fastest route. <strong>Ant colony optimisation</strong> sends ants through the road graph; at each junction an ant picks the next road with</p>
          <Formula note="τ = pheromone on the turn, η = 1 ÷ cost">P(turn) ∝ τ^α · η^β</Formula>
          <Formula note="evaporation, then the best ants deposit">τ ← (1 − ρ) · τ ;  τ ← τ + Q ÷ route time</Formula>
          <p>The three best routes are offered with probabilities inversely proportional to their travel time, which spreads cars over several roads. A car changes route only for one at least 5 % faster, at most once every 15 s.</p>
        </section>

        <section id="incidents" className="card">
          <h2>Incidents and the work zone</h2>
          <p>A road is flagged when a connected car has been stopped on it for 30 s, more than 30 m from the stop line (queues at a red light are at the stop line). The flag reaches the TMC; navigation avoids the road and the signals give no green to an approach that cannot move.</p>
          <p><strong>Work zone.</strong> When a crash blocks one side of the J2–J3 road, both directions share the open lane of the other side, one at a time:</p>
          <Formula note="per turn; a side that has waited 90 s goes next">green = 8 s + 2.5 s × waiting vehicles, at most 40 s</Formula>
          <p>Between turns both ends stay red until the lane is empty, counted by entry and exit detectors, so opposite directions never meet. An ambulance gets the next turn. When the crash is cleared the road returns to normal.</p>
        </section>

        <section id="glosa" className="card">
          <h2>Eco-driving (GLOSA)</h2>
          <p>Roadside units broadcast when their lights turn green. An eco car that would arrive at red slows down to arrive just as the light turns green:</p>
          <Formula note="advice SLOW; below 18 km/h the advice is STOP, and none when the timing is not predictable">advised speed = distance to the stop line ÷ (time until green + 1 s)</Formula>
          <p>Fuel and CO₂ are computed with the HBEFA4 emission model.</p>
        </section>

        <section id="ga" className="card">
          <h2>Genetic algorithm</h2>
          <p>Parameters are tuned offline with a genetic algorithm (tournament selection, BLX-α crossover, bounded Gaussian mutation, elitism, cached fitness, parallel SUMO runs). It tunes on seeds 101–103 and is then checked on seeds 1–10, which it never saw. Details and convergence curves: <Link to="/ga">GA optimisation</Link>.</p>
        </section>

        <section id="evaluation" className="card">
          <h2>Evaluation</h2>
          <p>Every configuration is run on 10 random seeds. Tables give the mean and standard deviation; a configuration is compared with its baseline seed by seed (paired Wilcoxon signed-rank test). All runs also check for collisions.</p>
        </section>

        <section id="results" className="card">
          <h2>Results</h2>
          <p className="small muted">Simulation results on the 2×2 grid, mean of 10 seeds. They describe these scenarios, not real roads. Full tables with tests: <Link to="/results">Results</Link>.</p>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Table (scrolls sideways on small screens)">
            <table className="data left">
              <thead><tr><th>Question</th><th>Result</th></tr></thead>
              <tbody>
                <tr><td>Adaptive against fixed-time signals</td><td className="wrap">Waiting 19.4 s → 12.1 s (−37.6 %) in normal traffic; 37.4 s → 26.9 s (−28.1 %) when congested</td></tr>
                <tr><td>Queues over VANET, all cars connected</td><td className="wrap">11.9 s (−38.7 %) normal; 22.2 s (−40.8 %) congested</td></tr>
                <tr><td>Queues over VANET, half the cars connected</td><td className="wrap">15.7 s (−18.9 %) normal; 20.2 s (−46.0 %) congested</td></tr>
                <tr><td>Ambulance priority</td><td className="wrap">Ambulance trip 87.6 s → 65.2 s with exact data, 65.6 s over VANET with 10 % loss and relaying; no missed preemption</td></tr>
                <tr><td>Ambulance route choice</td><td className="wrap">113.7 s → 63.3 s (−44 %) with a closure ahead; 74.1 s → 64.5 s (−13 %) in a jammed corridor</td></tr>
                <tr><td>Navigation with a closure</td><td className="wrap">A*: navigating cars −8.2 %, all cars −2.9 %; tuned ACO: −7.7 % and −3.2 %</td></tr>
                <tr><td>Eco-driving, fixed-time signals</td><td className="wrap">60 % eco cars: stops −25 %, waiting −19.6 %, CO₂ −1.2 %; 100 %: stops −40.5 %, waiting −30.6 %, CO₂ −2.9 %</td></tr>
                <tr><td>Work zone against closing the road</td><td className="wrap">Trips 102.1 s → 95.4 s (−6.6 %), waiting 19.4 s → 15.2 s (−22 %); no opposing vehicles in the shared lane, no collisions</td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <section id="limits" className="card">
          <h2>Limitations</h2>
          <ul>
            <li>Simulation only; the radio network is emulated in Python (range, loss, delay), not a full radio model.</li>
            <li>A small 2×2 grid with two signal phases per junction and no pedestrians; few alternative routes limit what navigation can gain.</li>
            <li>Speed advice needs predictable signal timing: it works with fixed-time signals, not with adaptive ones.</li>
            <li>The work zone exists on one road; one viewer at a time can drive a car, and runs with a driver are not used for evaluation.</li>
            <li>The signal controllers are rule-based; published look-ahead methods (for example Cai et al., Electronics 2019) are not implemented.</li>
          </ul>
        </section>

        <section id="future" className="card">
          <h2>Future work</h2>
          <ul>
            <li>Look-ahead signal control that predicts waiting time from approaching vehicles.</li>
            <li>Larger networks from real maps, pedestrians and more signal phases.</li>
            <li>A full radio simulator (OMNeT++/Veins) for the network layer.</li>
            <li>Coordination between neighbouring junctions (green waves) and several work zones.</li>
          </ul>
        </section>

        <section id="tech" className="card">
          <h2>Technologies</h2>
          <p>SUMO 1.28 with TraCI · Python 3.12 (NumPy, SciPy, Matplotlib) · Node.js (Express, Socket.io, SQLite, Zod, bcrypt, Helmet) · React, Vite, Recharts · pytest, Vitest, Supertest, Playwright.</p>
        </section>
      </article>
    </div>
  );
}
