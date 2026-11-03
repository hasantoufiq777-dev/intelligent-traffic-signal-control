# 5-minute demo script

**Before the audience arrives**
- Run `npm start`, or `npm run dev`, with `LIVE_LOOP=false` in `server/.env`, so you choose each scenario yourself.
- Open http://localhost:4000 (dashboard) and http://localhost:4000/admin (token from `server/.env`) in two tabs.
- Optional: click **Open in SUMO-GUI** to show SUMO's own window next to the dashboard.
- Make sure `results/summary.json` exists (`npm run sim:experiments`) so the Results page has numbers.

All runs below are on the 2×2 grid: set **network = grid2x2** in the admin form.

---

## 0:00-0:45 Baseline: fixed-time vs adaptive signals
1. **Admin:** scenario `normal`, controller `fixed`, Start.
2. **Live overview** (`/live`): the four junction cards cycle through their greens on a fixed timer, whatever the queues are. Point at the "Queued vehicles" chart.
3. **Admin:** controller `adaptive`, Start. *"The same traffic, but each junction now gives green where the queue is. Waiting time drops."*

## 0:45-1:45 VANET sensing at low penetration
1. **Admin:** controller `adaptive_vanet`, penetration `0.5`, Start.
2. **Junction cards:** blue bars are the true queue, orange bars the queue the roadside unit estimates from beacons of the 50% equipped cars (scaled ×2).
3. **VANET panel:** delivery ratio, latency, messages sent and lost. *"The controller only sees what the network delivers."*

## 1:45-3:00 Ambulance with V2I/V2V alerts
1. **Admin:** scenario `emergency`, controller `adaptive_ev_vanet`, loss `0.1`, relay `on`, Start.
2. **My drive:** click **Follow the ambulance** once it appears, about 30 s after the start at the default pacing.
3. **Event log:**
   - `alert_sent` / `alert_delivered`; with 10% loss some alerts are lost (`alert_lost`) and the next one, 0.5 s later, gets through;
   - `preempt_start` at J0 and the junctions ahead on its route;
   - `preempt_end` after it passes.
4. *"Only the ambulance's approach turns green, reached through yellow and all-red, so the cars in front of it, including those turning left, clear the way. Then the cross traffic gets a recovery green."*

## 3:00-4:00 Eco-driving and navigation
1. **Admin:** scenario `normal`, controller `fixed`, eco_fraction `0.6`, Start.
2. **My drive:** **Follow an eco car**. The card shows the next light, its countdown and the advice ("Ease off to 32 km/h"). The yellow dot on the map marks cars slowing for green. The Eco-driving panel compares CO₂ and stops of eco cars with the others.
3. **Admin:** scenario `navigation`, controller `adaptive_vanet`, nav `astar`, Start, then the same with nav `aco`.
   - Navigation panel: trip time of navigating vs other cars, and how evenly traffic spreads over the roads.
   - *"A\* sends everyone to the best route; ACO spreads cars over good alternatives."*

## 4:00-5:00 Results
1. Open **Results** (`/results`) and go through the tabs.
   - **Signals:** waiting time vs penetration against fixed and oracle adaptive.
   - **Emergency:** ambulance travel time vs packet loss, relay on and off, against oracle preemption.
   - **Navigation:** A\* vs ACO.
   - **Eco-driving:** CO₂ and stops vs eco-car share.
2. Each table gives mean ± std over 10 seeds, % improvement vs the baseline, and a paired p-value.
3. Close with the limits: emulated channel (no MAC layer), small grid, fuel/CO₂ from a model.

**If something goes wrong:** set `USE_MOCK=true` in `server/.env` and restart. The mock streams the same kinds of data without SUMO.
