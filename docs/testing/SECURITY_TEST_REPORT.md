# Security test report

Security tests across all phases, with the defects they found. Final state: 12 November 2026 (environment:
[test plan §1](TEST_PLAN.md#1-environment-all-reported-runs)). The threat model and mechanisms are in
[ARCHITECTURE.md §4](../architecture/ARCHITECTURE.md#4-security-model).

## 1. Summary

| Area | Tests | Result |
|---|---|---|
| Authentication and sessions | gateway tests (`auth.test.js`), browser role journeys | pass |
| Authorisation (roles) | 401/403 on every route per role, socket events, browser journeys | pass |
| CSRF and origin checks | API writes and live sockets | pass (socket origin check added in Phase 10) |
| Input handling | hostile bodies, parameters, socket payloads | pass |
| Response hardening | headers, CSP, error bodies, production check | pass (CSP narrowed in Phase 10) |
| Availability | rate limits, lockout, connection cap, sign-in burst | pass (hashing moved off the main thread in Phase 8) |
| Dependencies | `npm audit` in server, dashboard and root | production: 0; development tools: 2 moderate left (§6) |
| External penetration test / dynamic scanner | — | **not done** (§7) |

## 2. Authentication and sessions

**Procedure.** `server/test/auth.test.js` (30 tests), `tests/e2e/roles.mjs`, `tests/deploy/prodcheck.mjs`.
**Expected and actual** (all pass on the final run):

- **Passwords.** Stored as salted Argon2id (a separate salt per user). A malformed stored hash never
  matches. An old bcrypt hash works once and is replaced at sign-in. At least 10 characters; the
  72-byte limit is enforced.
- **Sign-in answers.** A wrong password and an unknown e-mail get the same answer (no account
  enumeration); "forgot password" answers the same for both too.
- **Lockout.** After `LOGIN_MAX_FAILS` (5) failures the account is locked (429); an administrator
  can unlock it. A per-address limit applies to sign-in, registration and reset attempts.
- **Session token.** 256 random bits in an `HttpOnly`, `SameSite=Lax` cookie, `Secure` in
  production (checked in a real browser over HTTPS). Only the SHA-256 is stored, so a database
  leak does not give usable sessions.
- **Session end.** Sessions end on sign-out, expiry, password change (the other sessions), password
  reset, disabling the account, and administrator revocation. Each also disconnects that session's
  live sockets (tested). At most `SESSIONS_PER_USER` per account.
- **Reset links.** Single use, expiring, stored as a hash; a second use is refused (browser test).
- **Registration.** Closed by default; self-registered accounts are always viewers.

## 3. Authorisation

- All data routes answer 401 without a session.
- Every administration route answers 403 to viewers and researchers. The browser shows a refusal
  page for `/admin` and `/administration` opened directly, but the server's 403 is what protects
  them.
- Nobody can promote themselves. The last active administrator cannot be removed, demoted or
  disabled, and no one can delete their own account.
- A researcher can start, stop, pause, resume, change the speed of and restart only their own
  runs. Doing so to another user's run gets 403; replacing it gets 409. Viewers get 403 on all of
  these.
- Socket abuse changes nothing and does not disconnect the server: a viewer's driver commands for
  a seat they do not hold, forged resume keys, injection text, wrong payload types and made-up
  event names.
- Refused actions are written to the audit log, which never contains passwords or hashes.

## 4. CSRF, origins, input

| Test | Result |
|---|---|
| Write without a CSRF token, with a guessed token, with another session's token | 403 `csrf` |
| Write from another origin (`Origin: https://evil.example`) | 403 `bad_origin` |
| Origin listed in `CORS_ORIGIN` | accepted with credentials |
| **Live socket with a valid cookie from another site** (Phase 10) | `bad_origin`; own site, a `CORS_ORIGIN` entry and non-browser clients (no `Origin`) connect |
| Oversized body (> 10 KB) / broken JSON | 413 / 400, no stack trace |
| Negative seed, path in `network`, shell text in `scenario`, prototype keys (`__proto__`), unknown keys | 400, nothing started |
| SQL text in filters and ids | no effect (prepared statements everywhere) |
| Script text in a user name (stored XSS) | stored and returned as JSON data with `nosniff`, rendered as text by React |
| Path traversal (`..`, `..%2f`, `%2e%2e`) | never returns `.env`, `package.json` or the database |

## 5. Response hardening and production configuration

**Procedure.** `tests/deploy/prodcheck.mjs` against a production-mode gateway behind HTTPS
([data](data/final-prodcheck.txt)), plus `server/test/snapshot.test.js`.

| Check | Result |
|---|---|
| Content-Security-Policy | `default-src 'self'`; scripts only from this site; `frame-ancestors 'none'`; `object-src 'none'`; **`connect-src` only `'self'` and `wss://`/`ws://` of this host** (Phase 10; before, any WebSocket host was allowed) |
| A malformed `Host` header cannot inject into the CSP | replaced by a placeholder (test) |
| HSTS | in production only (`max-age=15552000; includeSubDomains`) |
| `X-Content-Type-Options: nosniff`, no `X-Powered-By`, `Referrer-Policy: no-referrer` | present / absent / present |
| Errors | `{error, code}`; no stack trace, file path or SQL in any tested response, including a database failure |
| `index.html` | `no-cache`; build files `immutable` |
| Browser sign-in over HTTPS with the strict CSP | live cars shown, no CSP violations |

## 6. Dependencies

`npm audit` on the final code:

| Package | Production dependencies | All (including development tools) |
|---|---|---|
| `server/` | **0** | 2 moderate: `minimatch` inside `nodemon` (development auto-restart only; denial of service through crafted glob patterns, which nodemon does not take from users). The suggested fix is a nodemon downgrade; left as it is |
| `client/` | **0** | **0** (after the Phase 10 upgrade to Vite 8 / Vitest 5) |
| root | **0** | **0** |

Advisories fixed during the project:

| Phase | Package | Advisory | Fix |
|---|---|---|---|
| 8 | react-router 6.30.2 | 3 high (open redirect → XSS), then 2 moderate | React Router 7.18.5 |
| 9 | compression 1.8.1 | high, denial of service (GHSA-vc2v-76pw-4v95) | 1.8.2 |
| 10 | vite 5 / esbuild, vitest 2 / tinypool | 2 critical (tinypool prototype pollution → RCE), 1 high, 3 moderate (dev server readable by any website) | Vite 8.3.8, Vitest 5.1.0, plugin-react 6.3.0 |

## 7. Availability

| Test | Result |
|---|---|
| 50 simultaneous sign-ins with bcrypt (Phase 8) | **defect**: 2 per second, server blocked (event-loop lag 110 ms) |
| 200 simultaneous sign-ins with Argon2id off the main thread | 15.2 per second; an API call during the burst answered in 1.5 ms |
| Connection cap: 1,100 connections at once | exactly 1,000 accepted, 100 refused with `capacity`, existing viewers unaffected |
| Rate limit and lockout | 429 as configured |

## 8. Defects found (all fixed)

1. Password hashing blocked the event loop: a denial-of-service risk (Phase 8).
2. Duplicate-account race gave a 500 (Phase 8).
3. React Router vulnerabilities (Phase 8).
4. The `compression` advisory, caught before release (Phase 9).
5. **The live socket accepted connections from other sites with the user's cookie** (Phase 10).
   `SameSite=Lax` already stopped this for other *sites*, but not for sibling subdomains of the
   same site; the socket carries driver commands.
6. **The CSP allowed WebSocket connections to any host** (Phase 10), weakening its protection
   against data being sent out by injected script.
7. The dashboard's development tools had critical advisories (Phase 10).

## 9. Not done

- **No external penetration test** and **no automated dynamic scanner** (for example OWASP ZAP or
  Burp). The tests above are written from the threat model by the same team that built the system.
- **HTTPS was tested** through a local test proxy with a self-signed certificate, not nginx with a
  real certificate. TLS settings (protocols, ciphers) are nginx's and were not tested.
- **No multi-factor authentication**, and no e-mail delivery of reset links. Administrators hand
  the links over.
- **One browser only** (Edge/Chromium): browser-specific CSP and cookie behaviour in Firefox and
  Safari was not tested.
