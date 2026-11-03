# Dashboard (React)

Vite + React 18 + Recharts + socket.io-client + react-router. Plain CSS with light/dark tokens.

## Pages

| Route | Page | What it shows |
|---|---|---|
| `/` | **My drive** (user panel) | Live traffic map of the grid (roads, signal heads, moving cars). Tap a car to follow it. **My car**: speed, next traffic light with a countdown, the eco-driving (GLOSA) advice it receives, fuel and CO₂ used so far. Eco-driving summary (eco cars vs others) and key events. |
| `/live` | Live overview | Junction cards (true vs VANET-estimated queues, signals with R/Y/G letters, phase, ambulance ETA), VANET panel (delivery ratio, latency, messages), navigation panel, 120 s charts (queues, waiting time, throughput), full event log. |
| `/results` | Results | Reads `GET /api/experiments/summary`: tabs Signals, Emergency, Navigation, Eco-driving; charts with 95% CI error bars and tables (mean ± std, % vs baseline, paired p-value). |
| `/admin` | Admin (not in the menu) | Admin token, start/stop the live run (scenario, controller, nav, seed, penetration, loss, relay, eco share), read-only playlist. |

Demo links: `/?follow=eco` or `/?follow=ambulance` open the user panel already following such a car.

Cars that are not eco cars still get **suggested** advice in the panel, computed in the browser with the same rule as the
simulation (`src/lib/glosa.js` mirrors `sim/vanet/glosa.py`; `src/lib/glosa.test.js` checks the same cases). They do not
follow it.

## Run

```bash
cd client
npm install
npm run dev        # http://localhost:5173, proxies /api and /socket.io to the gateway on :4000
npm test           # unit tests (GLOSA rule)
npm run build      # production build in client/dist (served by nginx, or by the gateway itself)
```

Start the gateway first (`cd server && npm run dev:mock` for the fake simulation). `VITE_API_URL` (see `.env.example`)
is only needed when the dashboard and the gateway are on different origins.

## How live data flows

`src/hooks/useLiveSocket.js` (`LiveProvider` + `useLive` / `useLiveSocket`) keeps one Socket.io connection for the whole app.

- Messages go into mutable buffers. React state is committed at most every 500 ms (about 2 renders per second).
- History is capped: 240 states, 200 events.
- `init` replaces the buffers on (re)connect.
- A new run clears the previous run's history.
- A followed car is re-subscribed after a reconnect.

## Accessibility and theming

- Signal states always carry a letter (R/Y/G) or text, and car types differ by size and outline as well as colour.
- The map has a text label, and every car has a tooltip.
- The theme follows the operating system, with a toggle for light / dark / auto, remembered per browser.
- Layouts collapse to one column on phones.
