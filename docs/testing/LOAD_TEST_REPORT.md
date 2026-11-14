# Load test report

**Objective.** The platform serves up to 1,000 concurrent signed-in users with acceptable latency,
enforces the 1,000-connection limit, and stays stable over time. Brief targets: API p95 < 500 ms,
p99 < 1,000 ms, ≥ 99 % successful connections, no dropped connections.

## 1. Environment and method

- **Machine.** Windows 11, i7-12700H (20 threads), 15.7 GB RAM, Node 24.14.1. The test clients run
  on the same machine as the gateway, so there is no network latency, and the clients' CPU
  competes with the server's.
- **Gateway.** Final code, mock simulation playing a live run (`USE_MOCK=true`; per viewer the
  gateway does the same work as with SUMO, but SUMO's own CPU is not included).
  `MAX_CONNECTIONS=1000`. Rate limits raised, since all traffic comes from one address.
- **Tool.** `server/loadtest/steps.js`.
  - **Every user signs in first**, then opens a live Socket.io connection with the session cookie.
  - **Each step** holds N users for 20 s while the script calls the API (live status, run list,
    session, options) at about 32 requests per second.
  - **Server measurements** come from `/api/metrics`: CPU, memory, event-loop lag and outgoing
    bandwidth.
  - **Stream latency** is the server send time to client receive time of each state.

## 2. Results: final run (12 November 2026, final code, commit `608564b`)

Raw data: [`data/final-load-steps.json`](data/final-load-steps.json).

### 2.1 Steps

| Users | Connected | Connect p95 (ms) | Drops | Stream p50 / p95 / p99 (ms) | API p50 / p95 / p99 (ms) | API errors | CPU (one core) | RSS | Loop lag p99 | Out |
|---|---|---|---|---|---|---|---|---|---|---|
| 10 | 10 (100 %) | 22 | 0 | 2 / 3 / 3 | 2.8 / 5.0 / 6.8 | 0 | 7.9 % | 94 MB | 13 ms | 34 KB/s |
| 100 | 100 (100 %) | 35 | 0 | 5 / 10 / 11 | 2.5 / 4.6 / 11.8 | 0 | 5.2 % | 114 MB | 14 ms | 344 KB/s |
| 500 | 500 (100 %) | 44 | 0 | 14 / 28 / 31 | 2.4 / 5.1 / 27.9 | 0 | 8.2 % | 151 MB | 15 ms | 1.6 MB/s |
| 1,000 | 1,000 (100 %) | 30 | 0 | 25 / 39 / 43 | 2.8 / 20.3 / 40.8 | 0 | 9.8 % | 215 MB | 17 ms | 3.1 MB/s |

Every user received 0.95–1.0 states per second (the stream is one per second, sent as droppable
for slow clients).

### 2.2 Limit, spike and reconnection

| Test | Expected | Actual | Status |
|---|---|---|---|
| Spike: 1,100 connections at the same moment | 1,000 accepted, the rest refused with a clear message, no failures | **1,000 accepted, 100 refused with `capacity`, 0 failed**; all accepted within 0.66 s; 0 drops in the 15 s after; stream p95 38 ms; API p95 17.5 ms; loop lag p99 17.4 ms | pass |
| Reconnect: 500 of 1,000 users dropped at once, reconnecting immediately | all back | **500 / 500 back within 0.34 s** (connect p95 283 ms); 1,000 connected after | pass |

### 2.3 Against the targets (1,000 users)

| Target | Measured | Status |
|---|---|---|
| API p95 < 500 ms | 20.3 ms | pass |
| API p99 < 1,000 ms | 40.8 ms | pass |
| ≥ 99 % successful connections | 100 % at every step | pass |
| No dropped connections | 0 | pass |
| Limit of 1,000 enforced | exactly 1,000 of 1,100 | pass |

## 3. Earlier runs, kept for comparison

| Run | Steps (1,000 users) | Spike: all admitted after | Reconnect: all back after | Data |
|---|---|---|---|---|
| Phase 2–3 (ramp 1,050 users) | 1,000 connected, 50 refused; stream p95 79 ms; CPU 8.4 % | — | — | [Phase 2–3 report](PHASE_2_3_REPORT.md) |
| Phase 8 | API p95 55 ms, stream p95 81 ms, CPU 9.1 %, 210 MB | 5.7 s | 2.8 s | [`load-steps.json`](data/load-steps.json) |
| Phase 9 (after optimisation) | API p95 15.5 ms, stream p95 34 ms, CPU 8.3 %, 184 MB | 0.70 s | 0.34 s | [`phase9-load-steps.json`](data/phase9-load-steps.json) |
| **Final** | API p95 20.3 ms, stream p95 39 ms, CPU 9.8 %, 215 MB | 0.66 s | 0.34 s | [`final-load-steps.json`](data/final-load-steps.json) |

The large improvement between Phases 8 and 9 comes from the smaller first message: a new or
reconnecting viewer now receives 64 KB instead of about 350 KB
([performance report](PERFORMANCE_TEST_REPORT.md)).

**Soak, 30 minutes at 1,000 users (Phase 8, [`load-soak.json`](data/load-soak.json))**

- 1,000 connected in every sample; 0 drops, 0 API errors, 0 storage errors.
- Worst minute: stream p95 147 ms; API p95/p99 92/147 ms; event-loop lag p99 54 ms.
- Memory: RSS flat at 230–232 MB over the last 14 minutes; the JavaScript heap rose and fell
  between 33 and 86 MB with no upward trend.
- The simulation was still RUNNING at the end.

The soak test was not repeated after Phases 9–10. Those phases reduced the work per connection
(smaller first message), added an origin check to the handshake and a playback field to
`now_playing`; they did not change the per-second broadcast path.

**Sign-in burst (Phase 8):** 200 users signing in at once all succeeded at 15.2 per second
(before the fix: 2 per second with bcrypt).

## 4. How to run

```bash
# gateway with raised limits (all test traffic comes from one address)
cd server
USE_MOCK=true LIVE_LOOP=true AUTH_RATE_LIMIT=100000 RATE_LIMIT_PER_MIN=1000000 SESSIONS_PER_USER=2000 \
  ADMIN_EMAIL=loadtest@example.org ADMIN_PASSWORD='<10+ characters>' npm start

# in another terminal (password from LT_PASSWORD)
LT_PASSWORD='<same>' node loadtest/steps.js --url http://127.0.0.1:4000 --email loadtest@example.org \
  --steps 10,100,500,1000 --hold 20 --rps 40 --spike 1100 --reconnect 500 --out result.json
# soak: --steps 1000 --soak 30        sign-in burst: --logins 200
```

On Linux, raise the open-file limit for both processes first (`ulimit -n 65535`).

## 5. Limitations

- One machine for clients and server, and no real network. A deployment adds network latency and
  TLS, while the server has the CPU to itself.
- The mock simulation, so SUMO's CPU (about one core per run) is not in the figures; it runs as a
  separate process and does not share the gateway's event loop.
- The brief names k6 or Artillery. A Node script was used instead because it signs in each user
  and speaks Socket.io's protocol directly; the measured quantities are the same.
- The limit is per gateway process. Several gateways would need a shared counter, which is not
  built.
