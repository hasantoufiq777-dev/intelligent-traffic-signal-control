# Platform Implementation Plan and Audit

Plan for turning the research project into a secure multi-user platform, following
`vanet_intelligent_traffic_platform_master_prompt.md`. Work is done in phases; after each phase the
test suites are run and this file's status table is updated. Nothing is marked done before it is tested.

## 1. Audit of the existing system (Phase 1)

| Area | State before this work |
|---|---|
| Simulation (`sim/`) | Complete and tested (129 pytest tests at the start; 134 after Phase 10's playback tests): controllers, VANET, preemption, navigation (A*, ACO), incidents, GLOSA, GA tuning, driver mode, accident zone. **Must not change behaviour.** |
| Gateway (`server/`) | Express 5 + Socket.io + SQLite. One shared simulation process, playlist loop, throttled broadcast (load-tested with 1000 viewers). 19 tests + 2 real-SUMO e2e. |
| Dashboard (`client/`) | React 18 + Vite: My drive (map), Live overview, Results, Admin. 9 unit tests. |
| Authentication | **None.** Everyone could watch; admin actions used one shared `X-Admin-Token` typed into the browser. |
| Authorisation | Token or nothing. No roles, no users, no audit trail. |
| Sockets | Unauthenticated; no connection cap. |
| Security headers / rate limits / CSRF | None. CORS default `*`. |
| Database | Schema created with `IF NOT EXISTS` plus two ad-hoc column migrations; no migration versioning. |
| Lifecycle | Running / not running. No explicit STARTING/STOPPING/FAILED, crash retry unbounded, concurrent start/stop not rejected. |
| Map | SVG map with cars, signals, zone overlay; no zoom/pan, layers, RSUs, queues or routes. |
| Tests | No Playwright, no security tests, load test only for anonymous viewers. |

Found defects to fix: unbounded crash-retry loop of the playlist; `CORS_ORIGIN=*`; admin token kept
in browser `sessionStorage`; `/api/metrics` and run history readable by anyone.

## 2. Architecture decisions

| Decision | Choice | Reason |
|---|---|---|
| Simulation processes | One shared SUMO/Python process (unchanged) | Required; 1000 viewers share one stream |
| Database | **SQLite (WAL) kept** for all environments | One gateway instance; writes are small (events, 1 state sample / 2 s, sessions). PostgreSQL would add an operational dependency without a measured need. The data layer stays behind `db.js` so it can be swapped. |
| Redis | **Not used** | Single gateway instance; the Socket.io adapter and connection cap are in-process. Needed only for horizontal scaling (documented in the deployment guide). |
| Map | **SVG map kept and extended** (zoom, pan, layers) instead of Leaflet/OpenStreetMap | The networks are synthetic SUMO grids with no geographic coordinates; placing them on OSM tiles would show roads that do not exist. |
| Password hashing | Argon2id with Node's built-in `crypto.argon2` (19 MiB, 2 passes), scrypt on older Node; two hashes at a time on the thread pool | Phase 8 measured 2 sign-ins per second with pure-JavaScript bcrypt on the main thread; native hashing has no native build step either. Old bcrypt hashes are upgraded at sign-in |
| Sessions | Opaque random token in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` in production); only its SHA-256 is stored | Revocable server-side; nothing readable by scripts |
| CSRF | Per-session token in `X-CSRF-Token` on every state-changing request + Origin check | Works with cookies and with the same-origin SPA |
| Roles | `ADMIN`, `RESEARCHER`, `VIEWER`, enforced in backend middleware and on the socket | Frontend checks are only for presentation |
| `X-Admin-Token` | **Removed** | Replaced by role-based sessions; the first admin is created from `ADMIN_EMAIL` / `ADMIN_PASSWORD` or `npm run create-admin` |
| Password reset | Single-use, expiring reset link issued by an admin (and by SMTP later if configured) | No mail server in the project; nothing is faked |
| Connection cap | `MAX_CONNECTIONS` (default 1000) enforced in the Socket.io handshake | Extra connections get a clear `capacity` error; existing ones are untouched |
| JSON-lines protocol | Unchanged; additions are optional keys | Python stays the source of truth |

Role permissions:

| Action | VIEWER | RESEARCHER | ADMIN |
|---|---|---|---|
| Watch live traffic, results, docs; follow a car; drive (one-driver mode) | yes | yes | yes |
| Start a run on the shared simulation | no | yes | yes |
| Stop a run | no | own runs | yes |
| Driver mode on/off, end a drive, SUMO-GUI | no | no | yes |
| Users, roles, settings, audit log, sessions, server metrics | no | no | yes |

## 3. Phases and status

| Phase | Content | Status |
|---|---|---|
| 1 | Repository audit, baseline test run, this plan | done |
| 2–3 | Database migrations; users, sessions, RBAC, CSRF, security headers, rate limits, login lockout, audit log; authenticated sockets; 1000-connection cap; simulation lifecycle states, start/stop locking, bounded crash retry; login / account / user-management pages; backend tests | **done** — results in `docs/testing/PHASE_2_3_REPORT.md` |
| 4 | Design system: sidebar + top bar layout, shared components, responsive (1920 → 360), accessibility | **done** — `docs/testing/PHASE_4_5_REPORT.md` (automated accessibility audit moves to Phase 8) |
| 5 | Traffic map: zoom/pan/fit, layer controls, RSUs and radio range, queue counts, signal countdowns, junction and vehicle detail panels, closures, alternate and ambulance routes, smoother movement | **done** — `docs/testing/PHASE_4_5_REPORT.md` |
| 6 | Module pages: signals, VANET, emergency, navigation, incidents, work zone, GLOSA, GA, experiments, notifications, documentation, administration (health, sessions) | **done** — `docs/testing/PHASE_6_7_REPORT.md` |
| 7 | Research dashboards and downloadable reports; guided teacher demonstration mode | **done** — `docs/testing/PHASE_6_7_REPORT.md` |
| 8 | Testing: Playwright end-to-end and screenshots at all viewports, security tests, database tests, failure/recovery tests, load tests 10 → 1000 authenticated users, reports in `docs/testing/` | **done** — `docs/testing/PHASE_8_REPORT.md` (load tests use a Node script, `server/loadtest/steps.js`, instead of k6/Artillery; one target not met: load time on a slow link, carried to Phase 9) |
| 9 | Profiling and optimisation with before/after measurements | **done** — `docs/testing/PHASE_9_REPORT.md` (slow-link load 5.6–9.2 s → 1.1–1.9 s; first socket message 350 → 64 KB; HTTP gzip; retention index) |
| 10 | Deployment guide, production `.env.example`, backup/restore, final acceptance report | **done** — `docs/deployment/DEPLOYMENT.md`, `deploy/`, `docs/testing/FINAL_ACCEPTANCE_REPORT.md` and the test reports; also built the missing playback controls (pause, resume, speed, restart) and fixed the defects found while verifying (socket origin, CSP, dev-server sign-in, accessibility, dev-tool advisories) |

## 4. Rules kept through all phases

- The simulation's algorithms, execution order and results are not changed; stored results must
  reproduce (regression check against `results/raw.csv`).
- No fabricated live data: a page shows simulation data or says that none is available.
- Every phase ends with: Python tests, gateway tests, client tests, build, and an update of the docs.
