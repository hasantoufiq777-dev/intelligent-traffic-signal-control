# Incident experiments (Module 10)

Means over seeds. Detection rate = share of seeds where the TMC learned of the incident
while it was active. Added delay = mean travel time with the incident minus the same run without it.
Entries = vehicles that drove onto the incident edge more than 5 s after detection.

| Type | Nav | Penetration | Detection rate | Detection delay (s) | False flags | Rerouted veh | Entries nav / non-nav | Added delay (s) |
|---|---|---|---|---|---|---|---|---|
| closure | astar | 0.2 | 80% | 41.5 | 0.2 | 58.6 | 0.0 / 0.0 | 20.4 |
| closure | astar | 0.5 | 80% | 33.0 | 0.0 | 59.8 | 0.0 / 0.0 | 12.7 |
| closure | astar | 1.0 | 100% | 33.0 | 0.4 | 61.6 | 0.0 / 0.0 | 12.0 |
| closure | none | 0.2 | 80% | 41.5 | 0.4 | 57.0 | 0.0 / 0.0 | 17.6 |
| closure | none | 0.5 | 80% | 33.0 | 0.2 | 56.2 | 0.0 / 0.0 | 15.4 |
| closure | none | 1.0 | 100% | 33.0 | 0.2 | 54.6 | 0.0 / 0.0 | 14.2 |
| stop | astar | 0.2 | 0% | None | 0.0 | 3.6 | None / None | 0.3 |
| stop | astar | 0.5 | 40% | 35.0 | 0.0 | 9.6 | 0.0 / 32.5 | 1.3 |
| stop | astar | 1.0 | 100% | 33.6 | 0.0 | 22.8 | 0.0 / 29.2 | 0.3 |
| stop | none | 0.2 | 0% | None | 0.0 | 0.0 | None / None | 0.6 |
| stop | none | 0.5 | 40% | 33.0 | 0.0 | 0.0 | 0.0 / 37.5 | 0.1 |
| stop | none | 1.0 | 100% | 33.4 | 0.0 | 0.0 | 0.0 / 36.4 | 0.4 |
