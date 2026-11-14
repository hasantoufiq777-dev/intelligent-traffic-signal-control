# Test Report — Phases 1–3 (audit, accounts, access control, lifecycle)

Date: 2026-11-11. Machine: Windows 11, Intel Core i7-12700H (20 threads), 15.7 GB RAM, Node 24.14.1,
Python 3.12, SUMO 1.28. Every figure below was measured in these runs; nothing is estimated.

## 1. Summary

| Suite | Command | Result |
|---|---|---|
| Python simulation (baseline before any change) | `python -m pytest -q` | **129 passed** (6 min 28 s). No file under `sim/` was changed in these phases. |
| Gateway (mock simulation) | `npm --prefix server test` | **46 passed**, 2 skipped (the real-SUMO tests, run separately) |
| Gateway with real SUMO | `npm run test:e2e` | **2 passed** (ambulance run with V2I preemption; a signed-in user drives a car) |
| Dashboard unit tests | `npm --prefix client test` | **9 passed** |
| Dashboard build | `npm --prefix client run build` | succeeded |
| Browser check (Edge, Playwright) | `node tests/e2e/smoke.mjs <url> <email> <password>` | **10 of 10 steps passed** against the gateway with real SUMO |
| Load and capacity | `node server/loadtest/viewers.js --viewers 1050 …` | 1000 connected, 50 refused, 0 dropped (section 4) |

Before this work the gateway suite had 19 tests; all 19 still pass (now signing in instead of
sending `X-Admin-Token`). 27 tests are new (`server/test/auth.test.js`).

## 2. What the new gateway tests cover

| Area | Checked behaviour |
|---|---|
| Sign-in | HttpOnly, SameSite=Lax cookie; `Secure` + HSTS when `COOKIE_SECURE` is on; bcrypt hashes stored; only the SHA-256 of the session token is stored; the same answer for a wrong password and an unknown email; malformed input and SQL-injection strings rejected |
| Lockout and limits | Account locked after `LOGIN_MAX_FAILS` failures (HTTP 429) and unlocked by an administrator; per-address rate limit on sign-in (429) |
| Sessions | Ended by sign-out, by expiry, by a password change (other sessions), by a password reset, when the account is disabled, and when an administrator revokes one; at most `SESSIONS_PER_USER` per account |
| CSRF | A write without the session's token, with a guessed token, with another session's token, or from another origin is refused (403); an origin listed in `CORS_ORIGIN` is accepted with credentials |
| Headers | Content-Security-Policy, `nosniff`, no `X-Powered-By`, no permissive CORS |
| Roles | All 14 data routes answer 401 without a session; viewers and researchers get 403 on every administration route and cannot promote themselves; a researcher starts and stops their own run but cannot replace or stop another user's; denied attempts are in the audit log |
| User management | Create, list (paged), change role, disable, delete; validation (password length, 72-byte limit, role); duplicate email; the last active administrator cannot be removed, demoted or disabled; no self-delete |
| Password reset | Single-use, expiring link issued by an administrator; only its hash is stored; "forgot password" answers the same for known and unknown emails |
| Registration | Closed by default; opened and closed from the settings; self-registered accounts are always viewers |
| Audit trail | Sign-ins, failures, user changes, resets, settings, simulation start/stop, revoked sessions; contains no passwords or hashes |
| Live sockets | Refused without a session or with a forged cookie; disconnected when the session ends |
| Connection limit | With a limit of 3: the next 4 connections are refused with code `capacity`, the 3 existing ones stay connected, one audit entry (not one per refusal), a place frees up when a viewer leaves. A burst of 20 simultaneous connections against a limit of 5 ends with exactly 5 |
| Lifecycle | STARTING → RUNNING → STOPPING → STOPPED is broadcast; a start or stop during another request gets 409; a crashed run gives FAILED; the playlist stops after `MAX_SIM_FAILURES` failed runs (exactly 3 runs, no fourth) and an administrator start recovers |
| Database | An older database is migrated in place with its data kept; migrations are idempotent; unique email (case-insensitive), role check, foreign keys, cascade on delete, transaction rollback, session and audit housekeeping |

## 3. Defect found and fixed during testing

**Driver seat ended right after being taken (existing bug, not caused by the new code).** If a drive
ended (for example by the idle timeout) and another viewer took the seat before the simulation's
final report for the first car arrived, the new drive adopted the first car's id from that report and
was ended at once with reason `run_ended`. It showed as an intermittent failure of
`driver.test.js › frees the seat after the idle time…` (2 of 3 runs). Fix in `server/src/driver.js`:
a seat only learns its car's id from a message about a car that does not belong to an earlier
drive. After the fix the driver tests passed 8 times in a row and in every full run.

## 4. Load and capacity test (measured)

Gateway with the mock simulation (`USE_MOCK=true`, playlist loop), `MAX_CONNECTIONS=1000`, on the
machine above. 1050 authenticated viewers ramped up over 40 s, then held for 60 s.

| Measure | Result |
|---|---|
| Connections accepted | **1000** (the limit) |
| Connections refused with `capacity` | **50** (all of the excess) |
| Dropped during ramp / hold | 0 / 0 |
| Viewers that received the initial snapshot | 1000 |
| State messages received in the hold | 59 000 (0.98 per viewer per second; the stream is 1 per second) |
| Delivery latency (server send → client receive) | p50 40 ms, p95 79 ms, p99 102 ms, max 151 ms |
| Gateway CPU during the hold | average 8.4 %, maximum 12.5 % of one core |
| Gateway memory (RSS) | average 122 MB, maximum 124 MB |

The clients ran on the same machine as the gateway, so network latency is not included.

Not yet done (Phase 8): the 10/50/100/250/500/750 steps, API latency percentiles under load, spike
and reconnection tests, and the 30-minute soak test.

## 5. Browser check (real SUMO)

`tests/e2e/smoke.mjs` drives Microsoft Edge: redirect to the sign-in page, a wrong password is
refused, sign-in shows the live map with cars, car positions change between frames, the pages Live
overview, Results, Run control and Administration open, the administration lists users and system
health, and after sign-out `/api/options` answers 401. No console errors or Content-Security-Policy
violations were reported.

## 6. Known limitations at this stage

- Password-reset links are handed over by an administrator; there is no mail server.
- The connection limit is per gateway process (one instance is deployed). Several instances would
  need a shared counter (Redis), which is documented as future work, not built.
- The rest of the master plan (design system, extended map, module pages, full end-to-end, security
  scan, load steps, deployment documents) is listed in `docs/architecture/IMPLEMENTATION_PLAN.md`.
