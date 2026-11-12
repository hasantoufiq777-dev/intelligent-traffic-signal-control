# Final acceptance report

**Project.** VANET-based intelligent traffic signal control: research simulation and web platform.
**Date.** 12 November 2026.
**Scope.** The acceptance criteria of the master brief (§22), checked on the final code, with the
evidence for each. Where a criterion is met only in part, or could not be tested, this report says
so and why.

## 1. Verdict

**All 26 criteria in §22 are met on the tested environment, two of them with stated limits:**

- **Load to 1,000 users.** It ran on one machine, with the mock simulation.
- **Deployment instructions.** They are complete, but the Linux/nginx/systemd path was not run;
  the production configuration was verified locally on Windows behind an HTTPS test proxy.

Deviations from the brief's *recommended* technologies are listed in §4, gaps in individual module
items in §3, and work not done in §5.

## 2. Acceptance criteria (brief §22)

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Existing simulation functionality is preserved | **Met** | `sim/` changed only by display output and wall-clock playback; 12-run zero-regression test passes; live mode reproduces stored results; every published figure matches `results/summary.json` ([regression report §1](REGRESSION_TEST_REPORT.md#1-the-simulation-is-unchanged)) |
| 2 | Existing Python tests pass | **Met** | 134 passed: the original 129 plus 5 for playback ([unit report](UNIT_TEST_REPORT.md)) |
| 3 | Backend tests pass | **Met** | 80 passed, 2 skipped (the real-SUMO tests, run separately) ([integration report](INTEGRATION_TEST_REPORT.md)) |
| 4 | Frontend tests pass | **Met** | 13 passed; browser suites below |
| 5 | Integration tests pass | **Met** | gateway with real SUMO: 3 passed (ambulance run, driven car, playback) |
| 6 | End-to-end tests pass | **Met** | journey 10/10, every page 24/24, roles 14/14, production browser check ([E2E report](E2E_TEST_REPORT.md)) |
| 7 | Security tests executed | **Met** (scope stated) | authentication, roles, CSRF/origin on API and sockets, hostile input, headers/CSP, production check 8/8, dependency audits 0 in production ([security report](SECURITY_TEST_REPORT.md)); no external penetration test or dynamic scanner |
| 8 | Database tests executed | **Met** | migrations of older databases, constraints, cascades, transactions, retention with its index, failure while unavailable and during writes, online backup, restore, rollback over a newer schema ([integration](INTEGRATION_TEST_REPORT.md), [failure and recovery](FAILURE_RECOVERY_REPORT.md)) |
| 9 | Responsive tests executed | **Met** | 18 pages × 6 widths (1920–360 px): 108/108; accessibility 0 violations in 45 + 2 audits ([responsiveness report](RESPONSIVENESS_REPORT.md)) |
| 10 | Load tests up to 1,000 concurrent users | **Met, with limits** | 10 → 1,000 signed-in users: 100 % connected, API p95 20.3 ms, stream p95 39 ms; 30-minute soak at 1,000. One machine, mock simulation, no network latency ([load report](LOAD_TEST_REPORT.md)) |
| 11 | Maximum concurrent connections enforced | **Met** | 1,100 at once → exactly 1,000 accepted, 100 refused with a message, 0 failures; burst tests in the gateway suite |
| 12 | Shared simulation works correctly | **Met** | one run broadcast to all viewers; late joiners get history; lifecycle and playback states come from the simulation; ownership rules for researchers (gateway tests, role journeys) |
| 13 | Real-time updates are accurate | **Met** | states and map frames are the simulation's own output, at most one per second, latest-wins; follower details only to followers; stream p95 39 ms at 1,000 users; reconnecting viewers get the current snapshot |
| 14 | Traffic visualization is clear and prominent | **Met** | the live map is the home page and the centre of the demo; signals, countdowns, queues, congestion, closures, routes, RSUs and range, contraflow, layers, legend, zoom/pan/fit (Phases 4–5); reviewed at every width |
| 15 | Vehicles move according to actual simulation data | **Met** | each car drawn at the position, angle and type SUMO reports, animated between frames; browser test "cars move between frames"; no invented movement; paused or stopped runs are labelled as such on the map |
| 16 | Signals are visually understandable | **Met** | per-approach lights, countdowns, phase, controller, preemption (*AMBULANCE*), junction detail panel; Signals page with phase history |
| 17 | Accident and contraflow behaviour clearly visible | **Met** | closure *not yet detected* → detected, rerouting, ambulance route change, shared-lane direction arrows and clearance on the map and the Work zone page; demo steps 8–10 |
| 18 | Authentication and authorisation work correctly | **Met** | 30 gateway tests; 14 browser role steps; production cookie check over HTTPS |
| 19 | No critical security vulnerabilities unresolved | **Met** | none known; production dependency audits 0; the dev-tool criticals removed (Vite 8 / Vitest 5); 7 security defects found and fixed ([security report §8](SECURITY_TEST_REPORT.md#8-defects-found-all-fixed)) |
| 20 | No known critical application bugs unresolved | **Met** | every defect found in Phases 2–10 fixed with a test reproducing it (§6); no open defect |
| 21 | No unhandled failures in completed test scenarios | **Met** | no failing test and no uncaught page error in the final runs; failure tests show controlled handling ([failure and recovery report](FAILURE_RECOVERY_REPORT.md)) |
| 22 | Memory and event queues bounded under tested loads | **Met** | 30 min at 1,000 users: RSS flat at 230–232 MB, heap without upward trend, loop lag p99 ≤ 54 ms; history, events and outgoing frames capped by design (latest-wins, droppable states) |
| 23 | Deployment instructions are complete | **Met, with limits** | [deployment guide](../deployment/DEPLOYMENT.md): environment variables, database and migrations, build and start, health checks, nginx, WebSockets, HTTPS, systemd, logging, backup, restore, rollback; production `.env` example; production check 8/8, backup/restore/rollback run. Linux/nginx/systemd not executed (no Linux host with `sudo`) |
| 24 | Documentation is complete | **Met** | architecture, API, database, deployment, user, administrator, teacher-demo and troubleshooting guides; test strategy, plan and the twelve reports; in-app documentation (§7) |
| 25 | Teacher demonstration mode works | **Met** | 13-step guided demo with start, pause-to-explain and resume; browser test starts a step and pauses it; [teacher guide](../guides/TEACHER_DEMO_GUIDE.md) |
| 26 | Actual performance measurements documented | **Met** | [performance](PERFORMANCE_TEST_REPORT.md), [load](LOAD_TEST_REPORT.md), [Phase 9](PHASE_9_REPORT.md) with before/after; raw data in `data/` |

## 3. Feature requirements (brief §6–§7, §16) with partial coverage

Everything else in §6 and §7 is implemented (Phases 4–7 reports). These items are covered only in
part:

| Item | What exists | What does not |
|---|---|---|
| §6.6 Simulation playback | start, stop, **pause, resume, speed 0.5×–4×, restart** (added in Phase 10), clock, scenario, state; conflicting operations refused (409) | — |
| §7.5 VANET jitter | configured jitter shown (base 10 ms, jitter 5 ms) with measured mean delay | measured jitter is not streamed by the simulation |
| §7.7 ACO route probabilities | the method and formula are explained on the page | the probabilities stay inside the simulation; the page says so |
| §7.13 Configure vehicle counts | scenarios define demand (normal, congested, …); every other listed parameter is configurable | no free vehicle-count input; demand is chosen through the scenario |
| §7.15 Event centre: simulation started/stopped/failure, system warnings | the event centre's *Simulation* panel shows the run, its state, the last status and repeated failures; traffic events in the list and the bell | simulation status changes and system warnings (for example the connection limit) are not entries in the bell's count; administrators see them in the audit log and system health |
| §9 entities `notifications`, `incidents`, `accident_zones`, `experiments` as tables | events (incidents, zone, preemption) are stored per run in `events`; experiments are `runs` + `results` | no separate tables for these; the data is in `events`, `runs` and `results` |

## 4. Deviations from the recommended architecture (brief §3, §11, §17)

| Recommendation | Used | Reason and consequence |
|---|---|---|
| PostgreSQL in production | SQLite (WAL) | one gateway is the only writer; online backup; tested at 1,000 users. PostgreSQL would be needed for several gateways |
| Frontend on Vercel, backend on Render/Railway | one server: nginx + gateway + simulation | the simulation is a persistent child process; the session cookie (`SameSite=Lax`) needs the dashboard and API on the same site |
| Redis adapter | none | one gateway carried 1,000 users at ~10 % of a core; multi-gateway scaling is documented as not built |
| k6 or Artillery | Node script (`server/loadtest/steps.js`) | signs each user in and speaks the Socket.io protocol; same measurements |

## 5. Not done

- External penetration test or automated dynamic scan (for example OWASP ZAP).
- Deployment run on Linux with nginx, systemd and a real certificate.
- Screen-reader testing (NVDA, JAWS, VoiceOver), and testing with users.
- Firefox and Safari (only Edge/Chromium was driven).
- Multi-factor authentication and e-mail delivery of reset links (administrators hand links over).
- Load tests across a real network, and with several gateways.

## 6. Defects found and fixed during the platform work

| Phase | Defect |
|---|---|
| 2–3 | driver seat ended by a late report of the previous drive |
| 4–5 | mobile buttons on desktop; dropdown overflow at 390 px; empty but slow roads coloured congested |
| 6–7 | Administration titled "Run control"; VANET estimate shown for non-VANET controllers |
| 8 | sign-in hashing blocked the server; duplicate-account race (500); second driver-seat race; database write failure stopped the stream; React Router advisories; 27 accessibility issues |
| 9 | slow load on a slow link (5.6–9.2 s → 1.1–1.9 s); retention query scanned 1.6 GB |
| 10 | live socket accepted other sites' pages; CSP allowed any WebSocket host; sign-in through the dev server refused; junction name without the visible *AMBULANCE* note; dev-tool critical advisories; **missing playback controls (§6.6)** built; driver commands sent while paused were dropped (found by the new test before release) |

## 7. How to reproduce

The [test plan](TEST_PLAN.md) lists every suite with its command and the order of a full run. The
[deployment guide](../deployment/DEPLOYMENT.md) and `tests/deploy/prodcheck.mjs` reproduce the
production check.
