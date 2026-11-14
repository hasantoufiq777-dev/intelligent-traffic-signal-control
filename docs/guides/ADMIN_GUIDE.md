# Administrator guide

For administrators of a running platform. Installing and updating the server is covered in the
[deployment guide](../deployment/DEPLOYMENT.md); everyday use in the [user guide](USER_GUIDE.md).

## 1. The first administrator

On the first start the gateway creates an administrator from `ADMIN_EMAIL` and `ADMIN_PASSWORD`
(at least 10 characters), if none exists yet. Sign in, change the password under *Account*,
then remove `ADMIN_PASSWORD` from the configuration.

You can also create an administrator on the server; it asks for the password, which never appears
in the shell history:

```bash
cd server && npm run create-admin -- admin@example.org "Full Name"
```

## 2. Users (*Administration → Users*)

- **Add a user:** e-mail, name, initial password (10+ characters), role.
- **Roles:**
  - **Viewer:** watches, follows and drives cars, reads the research pages.
  - **Researcher:** also starts and stops their own runs.
  - **Administrator:** everything.
- **Change a role** in the table. It applies to the user's next request; their live connection
  follows the session.
- **Disable** an account. The user is signed out at once and cannot sign in. Setting it back to
  *active* also lifts a lockout after failed sign-ins.
- **Delete** an account. Their runs and audit entries stay.
- **Password reset:** *Reset link* creates a single-use link, valid for 60 minutes, to pass on to
  the user. There is no e-mail server. Only the link's hash is stored, so copy it when it is shown.
- **The last active administrator** cannot be disabled, demoted or deleted, and nobody can delete
  their own account.

## 3. Sessions and audit log

- **Sessions** lists the active sign-ins with address, browser and last activity. *Sign out* ends
  one immediately, including its live connection.
- **Audit log** records, with time and address:
  - sign-ins and failed attempts;
  - account changes and reset links;
  - settings changes;
  - run starts and stops;
  - refused actions;
  - reaching the connection limit, at most once a minute.

  Filter it by action. Entries are kept for 180 days (`AUDIT_RETENTION_DAYS`) and never contain
  passwords.

## 4. Settings and system health (*Administration → System*)

- **Allow self-registration.** When on, visitors can create viewer accounts. Off by default.
- **System health** refreshes every 5 s:
  - live connections against the limit (1,000);
  - CPU, memory, event-loop lag;
  - broadcast counters; bad lines from the simulation; database write errors;
  - database status with user, run and session counts, and the applied migrations;
  - the simulation's lifecycle and recent failures.

## 5. The simulation (*Run control*)

- **Start a run.** Choose the scenario, controller and options, then start. It replaces the current
  run for every viewer. Researchers can start a run only when no other user's run is active.
- **Stop.** Ends the run and pauses the playlist until the next start.
- **Pause / Resume, Speed, Restart.**
  - Pause freezes the shared run for every viewer.
  - Speed plays it at 0.5×–4× of the normal pacing.
  - Restart begins the same run again from its first second.

  All three change only wall-clock pacing, so results are unaffected. A paused run still counts as
  running, so the playlist does not move on. Someone driving a car is handed back after 2 minutes
  without input, which includes time spent paused.
- **Ambulance.** *Send an ambulance* puts an ambulance on the road at once, at any moment of the run,
  for every controller and scenario.
  - Route: automatic (the crossing through the most junctions, three on the grid), or a chosen entry
    and exit road.
  - Every junction on its way gives it a safe green: yellow, all-red, then green on its approach only
    (when it is 18 s away), held until it has passed. With `fixed`, `adaptive` or `adaptive_vanet`,
    which have no ambulance priority of their own, priority is added to all junctions for the rest
    of that run; with `adaptive_vanet` the junctions learn about it from its V2I alerts.
  - At most 3 sent ambulances drive at the same time. The panel lists the last messages: where it
    was sent, and on arrival its travel time and at how many junctions it got green. The run's
    result (*Run history*) lists every ambulance sent.
  - A run into which an ambulance was sent is no longer comparable with the research results.
- **Playlist.** With `LIVE_LOOP=true` the runs in `server/playlist.json` play one after another.
  After a crash the next entry is tried 5 s later; after 3 failed runs in a row (`MAX_SIM_FAILURES`)
  the platform stays *Failed* until someone starts a run, so a broken installation does not restart
  forever.
- **Driver mode.** Switch it off to stop viewers from driving cars; *Release* ends the current drive.
- **SUMO-GUI.** Opens the live run in SUMO's own window on the server's screen. This only makes sense
  when the gateway runs on a desktop computer, for example during a presentation.

If the simulation shows **Failed**, the cause is in the server log:
`journalctl -u itsc-gateway` on Linux, or the terminal running the gateway. See
[troubleshooting](TROUBLESHOOTING.md).

## 6. Capacity

At most `MAX_CONNECTIONS` (1,000) live connections. Further visitors are refused politely and
their page retries. The load tests carried 1,000 signed-in viewers at about 10 % of one CPU core
([load test report](../testing/LOAD_TEST_REPORT.md)).

## 7. Data and retention

| Data | Kept |
|---|---|
| Runs, results, events, driver trips | until deleted |
| State samples (one per 2 simulated seconds) | 14 days (`SAMPLE_RETENTION_DAYS`) |
| Audit log | 180 days |
| Expired sessions | removed every 10 minutes |

Backups and restores: [deployment guide §9](../deployment/DEPLOYMENT.md#9-backup-and-restore).

## 8. Routine checks

- **Daily:** `/api/health` is OK, and the backup log shows a new backup.
- **Weekly:** look over the audit log for repeated failed sign-ins or refused actions.
- **Before each update:** take a backup, run `npm audit --omit=dev` in `server/` and `client/`,
  and apply the update on a test copy first if you can.
