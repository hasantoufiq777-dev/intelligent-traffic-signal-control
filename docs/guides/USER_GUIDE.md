# User guide

For anyone who signs in to watch the simulation (viewers) or to run experiments (researchers).
Administrators: see also the [administrator guide](ADMIN_GUIDE.md).

## 1. Signing in

Open the site and sign in with the e-mail and password an administrator gave you. If self-registration
is open, *Create an account* makes a viewer account.

- **Forgot your password?** Choose *Forgot password*; an administrator then sends you a one-time link.
  There is no e-mail server.
- **After 5 wrong passwords** the account is locked for 15 minutes; an administrator can unlock it.
- **You are signed out** after 12 hours, when you change your password elsewhere, or when an
  administrator ends your session or disables the account.

## 2. What each role can do

| | Viewer | Researcher | Administrator |
|---|---|---|---|
| Watch the live map and every module page | ✓ | ✓ | ✓ |
| Follow a car, drive a car (one person at a time) | ✓ | ✓ | ✓ |
| Research pages: results, GA, run history, documentation, guided demo | ✓ | ✓ | ✓ |
| Start and stop runs with chosen settings (*Run control*) | | ✓ (own runs) | ✓ (any run) |
| Users, sessions, audit log, settings, system health | | | ✓ |

Everyone watches the **same** live simulation. When a researcher or administrator starts a run,
every viewer sees it.

## 3. The screen

- **Sidebar.** *Monitor* (live pages), *Research*, and *Control* / *System* for researchers and
  administrators. On a phone it opens with the ☰ button.
- **Top bar.** Connection status (*Live*, *Reconnecting…*, *Offline*), the bell (new important
  events), the light/dark theme, and your account menu.
- **Now-playing bar** on the live pages: scenario, controller, simulated time, how many people are
  watching, and the run's state (*Starting*, *Live*, *Idle*, *Failed*).

## 4. The live traffic map

The map shows the 2×2 grid with junctions J0–J3 (in the `city` scenario also the ring road, bus
lanes and residential streets around it) and every vehicle of the running simulation, moved from the
simulation's own positions once per second.

Above the map, **Traffic control view** shows this overhead map; **Driver perspective** shows the
road in 3D from the seat of the vehicle you drive or follow (or from just behind it), with its speed,
the speed limit, the light ahead and the efficient speed. Both show the same simulation.

| Element | Meaning |
|---|---|
| Vehicle shapes | car, motorcycle, bus, truck, ambulance (white with a red cross); the vehicle you drive is purple |
| Green vehicle | an eco vehicle: always this one green, on the map, in the 3D view and in the legend |
| Blue roof dot | the vehicle has a VANET radio; vehicles without a dot are not connected. The counter at the bottom of the map shows how many are connected and the penetration (50 % by default) |
| Yellow dot | slowing for a green |
| Red-tinted lane | bus lane (buses and ambulances only) |
| Light grey street | residential street: 30 km/h, no trucks or buses |
| Hatched lane | closed to trucks and buses (they may not turn left at the signals) |
| Signal lights and numbers | each approach's light and the seconds until it changes |
| Number on an approach | cars queued there |
| Road colour | congestion from the measured road speed (green / amber / red) |
| Red cross, dashed road | accident or closure; *not yet detected* until a roadside unit notices it |
| Pink line | an ambulance's planned route |
| Antenna icon, circle | roadside unit and its radio range |
| Shared lane with arrows | the work zone's one-way traffic on the open lane |

**Controls**

- Zoom with the mouse wheel or *+ / −*; drag to pan; the frame button fits the whole network.
- From the keyboard, focus the map, then: `+` `-` to zoom, arrow keys to pan, `0` to reset.
- *Layers* switches parts of the map on or off; for example *VANET radio range* or *Road names*
  are off at first.
- Click a junction for its signals and queues; click a car to follow it. The side panel shows its
  speed, next light, the green countdown, the speed advice, and its fuel and CO₂.

## 5. Pages

| Page | What it shows |
|---|---|
| Live overview | junction cards, event log, queue, waiting-time and throughput charts, VANET and navigation panels |
| Signals | each junction's phase history and queues; how the current controller decides |
| VANET | message delivery ratio, loss, delay and measured jitter; each roadside unit's estimated queue against the true one |
| Emergency | the ambulance, its route, alerts and preemptions |
| Live traffic → Routes | the route explorer: pick a start, a destination and a vehicle type; the simulation's A* returns the fastest route now, the shortest and other alternatives, with distance, time at free flow, time now, signals and congestion, drawn on the map. It says when the shorter route is not the faster one |
| Navigation | trip times of navigating cars against the others; how evenly traffic spreads; the latest route decision (the A\* route, or ACO's alternatives with their probabilities and which was picked) |
| Incidents | accidents, when they were detected and how traffic reacts |
| Work zone | the shared lane: whose turn it is, queues at both ends, the ambulance's turn |
| Eco-driving | speed advice (GLOSA), stops, CO₂ of eco cars against the others |
| Events | all events, including when a simulation started, finished, was stopped or failed, and when the platform was full; the bell counts the important ones (not every start and finish) |
| Guided demo | thirteen steps through the project ([teacher guide](TEACHER_DEMO_GUIDE.md)) |
| Results | the stored experiment results over 10 seeds with statistics; CSV download |
| GA optimisation | the genetic algorithm's convergence and its check on unseen seeds |
| Run history | every live run; tick two finished runs to compare them |
| Documentation | the problem, the architecture, every algorithm with its formula, limitations |

The live pages show what the current run produces. With some controllers or scenarios a panel
stays empty and says why (for example, there are no VANET figures under fixed-time signals).

## 6. Driving a vehicle

In *Drive a vehicle* (tab *Vehicle*), choose the vehicle (car, eco car, motorcycle, bus or truck in
the `city` scenario), a start road and a destination, then *Take the car*. Only one person drives at
a time.

| Control | Key | Effect |
|---|---|---|
| Accelerate | W or ↑ | 5 km/h faster |
| Brake | X or ↓ | 5 km/h slower (to a stop below 5 km/h) |
| Slow down | E | to 60 % of the current speed |
| Stop | S or Space | brake to a stop (after the junction if you are in it) |
| Normal | G | drive normally, up to the limit |
| Desired speed | slider | hold at most this speed |

Your controls only set a target speed. It is never above the speed limit, and the simulation still
keeps safe gaps, stops at red lights and uses only the lanes your vehicle may use.

The panel shows your speed and the limit, the roadside unit's advice for the next light, and the
**estimated fuel-efficient speed** with its reasons: the limit, the light ahead (arrive on green
instead of stopping), the vehicle ahead, congestion, and the steady speed with the least fuel for
your vehicle. It also shows fuel, CO₂ and time for your speed against the recommended one, and a
chart of fuel against speed. *Drive at … km/h* applies it. Switch the map to *Driver perspective* to
see the road from your seat.

- Without input for 2 minutes, the car is handed back.
- A drive lasts at most 10 minutes.
- If your connection drops, you have 15 s to come back to your car.

At the end, a trip report compares your drive with the other cars: travel time, average speed,
stops per km, fuel and CO₂.

## 7. Running experiments (researchers)

*Run control* starts a run on the shared simulation. Choose:

- the scenario and the controller;
- navigation (none / A\* / ACO);
- the seed;
- VANET penetration, packet loss and relay;
- the share of eco cars and the ambulance route choice;
- the **demand**: how much traffic, as a factor of the scenario's own flows (0.25× to 2×; 1 = as defined).

*Stop* ends your run.

While your run plays you can also:

- **Pause** and **Resume** it; the map then says it is paused, at which simulated second;
- change its **speed** (0.5×, 1×, 2×, 4×);
- **Restart from the beginning** with the same settings;
- **Send an ambulance**, automatically through the most junctions or from a road you choose to an
  exit you choose. Every junction on its way turns green for it (*AMBULANCE* on the map), whatever
  the controller; when it arrives you see its travel time and at how many junctions it got green.

Pause and speed change only how fast the run is shown, not its results. You cannot stop, pause or
replace a run another user started; an administrator can.
Finished runs appear in *Run history*, where two runs can be compared side by side.

## 8. Account

*Account* (top-right menu): change your display name and password. Changing the password signs out
your other sessions.

## 9. When the platform is full

At most 1,000 people can watch at once. If it is full, you see a message and the page retries by
itself.
