# Next steps: from working prototype to full system

Status on 28 November 2026. As a **simulation and research platform** the system already works end to
end: three user roles (Administrator, Researcher, Viewer), live map, dynamic signal control
(`level_vanet`), eco cars, Docker deployment, a public demo link, and unit and system tests. What is
still missing depends on the goal: a **defence demo** or **real users**.

## 1. Must-have before the defence

| # | What | Why | Effort |
|---|---|---|---|
| 1 | **Commit and push** the Docker files, the new tests and the docs (branch `saikat`, pull request into `main`) | They exist only on one PC; the teammate and the supervisor cannot see them | 5 min |
| 2 | **Re-run the eco comparison for `level_vanet`** with `eco_commit 8` | The eco results on the dashboard were measured before queue levelling became the default | 30 min (runs unattended) |
| 3 | **Update the defence document** | It does not yet cover dynamic yellow and all-red, the free-road rule, Docker or testing | 20 min |
| 4 | **Demo accounts**: one Administrator, one Researcher, one Viewer in the Docker version | Shows the role-based access working during the defence | 5 min |

## 2. To run as a real online system

| # | What | Status now | Suggestion |
|---|---|---|---|
| 5 | **Permanent hosting** | Temporary Cloudflare link from a PC | Azure for Students VM with `docker-compose.prod.yml` (files ready, see [DEPLOYMENT.md §14](deployment/DEPLOYMENT.md#14-server-with-docker-and-automatic-https)) |
| 6 | **Automatic tests on every push (CI)** | No `.github/workflows`; tests run by hand | GitHub Actions running the unit tests on every push and pull request |
| 7 | **Password reset by e-mail** | An administrator issues the reset link and sends it by hand (no mail server) | A free e-mail service (e.g. Brevo, or Gmail SMTP) |
| 8 | **Automatic daily backup** | Backup script exists but is not scheduled in Docker | A daily backup job in compose, keeping the last 7 days |
| 9 | **Monitoring and uptime alerts** | Health check only | A free uptime monitor (e.g. UptimeRobot) checking `/api/health` |

## 3. Nice to have

| # | What | Benefit |
|---|---|---|
| 10 | **Install on a phone (PWA)** | Users add the dashboard to the home screen like an app |
| 11 | **Bangla / English language switch** | Easier for local users |
| 12 | **Downloadable report per run** (PDF or CSV: waiting time, fuel, CO₂) | Useful for researchers and for the thesis |
| 13 | **Real Dhaka road map** (an area imported from OpenStreetMap into SUMO) | Shows the system on real local roads instead of a test grid; strong for the defence |

## 4. Real-world limits (future work)

The system controls **simulated** traffic. Controlling real signals would also need:

- roadside radio units (RSU: DSRC or C-V2X) and in-car units, or a phone app that sends GPS positions;
- a link to real signal controllers (for example through the NTCIP standard);
- approval from the traffic authority.

The software is designed for this, because the signal controllers read traffic only through the
VANET layer. The hardware and permissions are outside the scope of a student project and are
presented as **future work**.

## 5. Recommended order

1. Items **1–4** now: these matter most for the defence.
2. If time allows: **6 (CI)** and **13 (Dhaka map)**. Both show good engineering practice and make a strong impression.
3. **5 (hosting)** only if the system must stay online after the defence; then 7, 8 and 9.
