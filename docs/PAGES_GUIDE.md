# What each page shows

The left sidebar groups the pages under **Monitor**, **Research**, **Control** and **System**. Every
page shows the simulation that is running now. Everyone signed in watches the same run, which
updates every second.

---

## Monitor

### Live traffic
The main page. It shows a map of the road network with every vehicle moving on it.
- **Traffic control view:** the 2D map from above. It shows:
  - cars, by type and colour;
  - traffic lights, with the seconds left in each colour;
  - congested roads, in yellow;
  - accidents and closed roads;
  - ambulances, with their routes.

  Click a car or a junction to see its details.
- **Driver perspective:** a 3D view from the driver's seat of the car you picked. A display on the
  screen shows its speed, the next signal, and the cars ahead and behind.
- **Tabs on the right:**
  - **Overview:** incidents, ambulances on the road and the latest events.
  - **Junction:** one junction's lights, queues and controller.
  - **Vehicle:** *My car* shows the car you picked: its speed, fuel, CO₂, next signal, and the cars
    ahead and behind it. *Drive* lets you take control of a car (go, ease off, stop, follow the
    car ahead).
  - **Routes:** the route explorer. Pick a start and an end road to see the fastest (A\*), the
    shortest and the alternative routes with their times.
  - **Eco-driving:** fuel and CO₂ of the eco-driving cars compared with the others.

### Live overview
The whole run on one page:
- which scenario and controller are running, and how many people are watching;
- one card per junction with its light and queue lengths;
- live charts of queued vehicles per junction, average waiting time and throughput;
- the event log, plus VANET and navigation summaries.

Use it to see how the run is going at a glance.

### Signals
How the traffic lights behave. By default they are **dynamic**: every second the controller measures
each road and gives green to the busier direction, for 5–45 s sized to its queue. Every change still
goes green → yellow 3 s → all red → green. The page shows:
- per junction, a table of each road: waiting cars, queue length, density, average wait, cars
  arriving per minute, how full the road ahead is (clear / busy / blocked) and its priority score.
  The roads with green are marked **priority**, with the planned green, the time used and left;
- the latest decision in words, such as "East–West selected: highest congestion score" or "North
  road green duration reduced: downstream road blocked", and the recent decisions;
- each junction's phase strip, so you see when it was green for which direction;
- a chart of vehicles waiting per junction;
- **Fixed-time vs dynamic control**: the same traffic run with each controller (waiting time, queue,
  vehicles through, travel time, total congestion, stops, throughput);
- a table of the moments an ambulance took priority (preemption).

### VANET
The radio network between cars and roadside units. Tiles show:
- the radio range, and the share of cars with a radio (penetration);
- how many messages arrived, how many were lost, and the delay and its spread (jitter);
- relay hops, and how early ambulance alerts arrived;
- missed preemptions.

There is a card per roadside unit (RSU), the ambulance alert counts (sent, delivered, lost) and a
message log.

### Emergency
Ambulances:
- **How it works:** the ambulance sends its position and route. A junction it will reach within
  18 s turns green for it, then returns to normal.
- **What it shows:** the ambulance's route, which junctions are giving it priority now, and a
  timeline of preemptions and route changes.

### Navigation
Cars that follow a navigation system ask for the fastest route about every 30 s.
- **A\*** gives the single best route.
- **Ant colony (ACO)** finds up to three good routes and spreads cars over them.

It shows the latest route decision with its reason, how a route is chosen, and a list of route
changes.

### Incidents
Accidents and closed roads.
- **Detection:** an accident is detected when a car with a radio has stood still for 30 s away from
  a traffic light.
- **What happens then:** the roadside unit reports it to the traffic centre, navigation avoids the
  road, and the lights stop giving green to a blocked approach.
- **What it shows:** when the incident started, when it is planned to clear, and an incident
  timeline.

### Work zone
The accident work zone, used in the `accident_zone` scenario. A crash blocks one side of the J2–J3
road, so the open lane carries both directions, one at a time, with automatic signals at both ends.
It shows:
- the current direction and the clearance time;
- the safety check: seconds with both directions in the lane, which must be 0;
- the number of direction changes and the expected wait each way;
- the rules and a zone timeline.

### Eco driving
Everything about eco cars. Select any car in the table to see its details: Eco %, fuel and CO₂ against the
SUMO HBEFA4 reference, average and most efficient speed, stops, stopped time, why it is an eco car, and the
advice it is getting now. The page also compares eco cars with other cars, and each run's selection results.

### Driving
Eco-driving with green-light speed advice (GLOSA). Each roadside unit broadcasts its signal timing (SPaT). An eco car
works out when it will reach the light and gets advice: **GO**, **SLOW** (to arrive just as the light
turns green), **STOP**, or none when the timing can't be predicted. It shows:
- how many SPaT messages arrived;
- stops per trip for eco cars compared with the others;
- for the car you picked: its speed, advised speed, seconds to green, and fuel and CO₂ so far;
- **Cars against the SUMO fuel model**: every car's measured fuel, CO₂, average speed, stops and time
  stopped, next to what SUMO's fuel-curve model (`sim/data/fuel_curves.json`) expects for the same
  distance driven smoothly. **Above model** is the fuel lost to stops, idling and accelerating. With
  *eco_select = data* (the default) the cars furthest above the model become the eco cars, up to the
  eco fraction, and the table says why each one was chosen. A fold-out shows the whole-network result
  of choosing by data, at random, or not at all, on the same traffic.

### Events
Everything the simulation reports, newest first: signal changes, preemptions, messages, incidents,
route changes, the zone and run status. Filter by group, or show only the important events. The
latest 200 are kept here; all events are also stored with their run.

---

## Research

Only **GA optimisation** and **Documentation** appear in the sidebar. The Guided demo, Results and
Run history pages are hidden from the menu but still open by address (`/demo`, `/results`, `/runs`).

### GA optimisation
The genetic algorithm that tunes parameters offline. Each candidate setting is scored by running
SUMO; the best ones survive and are combined. The page shows, for both targets (signal settings and
ACO settings):
- the convergence chart (fitness per generation);
- the chosen parameters;
- the check on seeds the tuning never saw.

### Documentation
The project explained in the app: problem and objectives, architecture, simulation method, signal
control, VANET, ambulance priority, A\* and ACO navigation, incidents and the work zone,
eco-driving, the genetic algorithm, evaluation, results, limits, future work and technologies.

---

## Control (admins and researchers only)

### Run control
Start and steer the simulation:
- **Live run control:** choose the scenario, network, signal controller, navigation (none, A\*,
  ACO), radio penetration, packet loss, relay and seed, then start the run.
- **Playlist:** the list of runs that play one after another when nobody starts one by hand.
- **Ambulance:** send an ambulance into the running simulation at any moment. Every signal then
  gives it priority.
- **Driver mode:** switch on or off whether viewers may drive a car (one at a time), see who is
  driving, and end their drive.

---

## System (admins only)

### Administration
Manage the platform:
- **Add a user** and **Users:** create accounts and set roles (admin, researcher, viewer).
- **Active sessions:** who is signed in now.
- **Audit log:** who did what and when (runs started, users changed and so on).
- **Settings:** open or close self-registration (new sign-ups always get the viewer role).
- **System health:** whether the gateway and the simulation are working.

---

**Account** (top-right menu): your profile and changing your password.
