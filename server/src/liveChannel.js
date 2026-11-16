// The one shared live simulation: process lifecycle, broadcast to all viewers, history, playlist loop.
import crypto from 'node:crypto';
import { DriverSeat } from './driver.js';
import { SimProcess } from './simProcess.js';
import { buildSimArgs, RouteQuerySchema, VehicleIdSchema } from './protocol.js';

/** Columns of the compact map frame sent to every viewer. */
const MAP_FIELDS = ['id', 'x', 'y', 'angle', 'flags', 'kind'];

const ROOM = 'live';
const VEHICLES_OFFSET_MS = 150; // keep the map frame out of the state broadcast's tick

/**
 * The part of a state that the dashboard's history charts read: time, metrics, each junction's
 * phase and queues, the VANET delivery ratio and the navigation averages. A new viewer gets 120 of
 * these in its first message; the full latest state is sent beside them. Signal timing (SPaT), road
 * speeds and eco data are only shown for the current second, so the history leaves them out
 * (about 2.9 KB -> 0.5 KB per state).
 */
export function historyRow(msg, runId = msg.run_id) {
  const tls = {};
  for (const [id, j] of Object.entries(msg.tls ?? {})) tls[id] = { phase: j?.phase, queues: j?.queues };
  const nav = msg.nav ? { avg_tt_nav: msg.nav.avg_tt_nav, avg_tt_non: msg.nav.avg_tt_non, edge_load_cv: msg.nav.edge_load_cv } : undefined;
  return { type: msg.type, run_id: msg.run_id ?? runId, t: msg.t, metrics: msg.metrics, tls,
    comm: msg.comm ? { pdr: msg.comm.pdr } : undefined, nav };
}

export class LiveChannel {
  constructor({ cfg, io, db, logger, playlist }) {
    this.cfg = cfg;
    this.io = io;
    this.db = db;
    this.log = logger;
    this.playlist = playlist ?? [];
    this.nextIndex = 0;
    this.paused = !cfg.LIVE_LOOP;
    // Show live runs in the SUMO-GUI window (toggled from the dashboard; never with the mock)
    this.gui = Boolean(cfg.SIM_GUI) && !cfg.USE_MOCK;
    this.closing = false;

    this.proc = null;
    this.current = null; // { run_id, entry, source, started_at, status, started_by }
    // Simulation lifecycle shown to every client: STOPPED -> STARTING -> RUNNING -> STOPPING -> STOPPED,
    // FAILED after an error, RECOVERING while the playlist waits to retry.
    this.lifecycle = 'STOPPED';
    // Pause and speed of the running simulation, as confirmed by it ('playback' messages)
    this.playback = { paused: false, speed: 1 };
    this.failures = 0; // consecutive failed runs
    this._op = false; // a start/stop/restart request is in progress
    this.accept = null; // (socket) => boolean, set by the gateway (session rooms, connection cap)
    this.lastStatus = null;
    this.lastResult = null;
    this.latestState = null;
    this.states = []; // last HISTORY_SECONDS of simulated time, as chart rows (historyRow)
    this.events = []; // last EVENT_HISTORY events
    this.lastSampleT = -Infinity;
    this.sawFinalStatus = false;
    this.network = null; // road geometry of the current run
    this.latestVehicles = null; // latest full vehicles message from Python
    this.vehicleIndex = new Map(); // vehicle id -> detail object (latest)
    this.followerOf = new Map(); // leader id -> the vehicle following it

    this._lastStateEmit = 0;
    this._lastVehiclesEmit = 0;
    this._pendingVehicles = null;
    this._vehiclesTimer = null;
    this._pendingState = null;
    this._stateTimer = null;
    this._loopTimer = null;
    this._viewerTimer = null;
    this._lastViewerCount = -1;

    this.stats = { started_at: Date.now(), lines_in: 0, bad_lines: 0, states_in: 0, states_broadcast: 0,
      events_broadcast: 0, bytes_broadcast: 0, runs_started: 0, persist_errors: 0 };
    this._lastPersistLog = 0;
    // One-driver mode: the only thing a viewer can control (one at a time)
    this.driver = new DriverSeat({ cfg, io, db, logger, live: this, room: ROOM });
    this._routeQueries = new Map(); // qid -> { cb, timer, socketId }
    this._routeSeq = 0;
  }

  /**
   * Route explorer: forward an A* query to the simulation and answer only the asking viewer.
   * At most one open query per viewer and one every 500 ms; 5 s timeout.
   */
  _routeQuery(socket, body, cb) {
    const parsed = RouteQuerySchema.safeParse(body ?? {});
    if (!parsed.success) return cb({ ok: false, error: parsed.error.issues[0]?.message ?? 'Invalid roads', code: 'invalid' });
    if (!this.proc || this.lifecycle !== 'RUNNING') return cb({ ok: false, error: 'No simulation is running', code: 'not_running' });
    const now = Date.now();
    if ((socket.data.lastRouteQuery ?? 0) > now - 500 || [...this._routeQueries.values()].some((q) => q.socketId === socket.id)) {
      return cb({ ok: false, error: 'One route search at a time, please', code: 'rate_limited' });
    }
    socket.data.lastRouteQuery = now;
    this._routeSeq += 1;
    const qid = `q${this._routeSeq}`;
    const timer = setTimeout(() => {
      this._routeQueries.delete(qid);
      cb({ ok: false, error: 'The simulation did not answer in time', code: 'timeout' });
    }, 5000);
    timer.unref?.();
    this._routeQueries.set(qid, { cb, timer, socketId: socket.id });
    if (!this.proc.send({ cmd: 'routes', qid, ...parsed.data })) {
      clearTimeout(timer);
      this._routeQueries.delete(qid);
      return cb({ ok: false, error: 'The simulation is not accepting commands', code: 'not_running' });
    }
    return undefined;
  }

  // ------------------------------------------------------------------ viewers
  attach({ accept = null } = {}) {
    this.accept = accept;
    this.io.on('connection', (socket) => {
      if (this.accept && !this.accept(socket)) return; // refused (over the connection limit)
      socket.join(ROOM);
      socket.emit('init', this.snapshot());
      // Viewers are read-only, except for the one driver seat (driver.js).
      socket.on('disconnect', () => this._scheduleViewerBroadcast());
      this.driver.attach(socket);
      // Following a car is a subscription (detail stream for one vehicle), not control.
      socket.on('follow', (id) => this._follow(socket, id));
      socket.on('unfollow', () => this._follow(socket, null));
      // Route explorer (read-only): the simulation's A* answers for this viewer only
      socket.on('routes', (body, cb) => this._routeQuery(socket, body, typeof cb === 'function' ? cb : () => {}));
      this._scheduleViewerBroadcast();
    });
  }

  viewers() {
    return this.io.of('/').sockets.size;
  }

  _scheduleViewerBroadcast() {
    if (this._viewerTimer || this.closing) return;
    this._viewerTimer = setTimeout(() => {
      this._viewerTimer = null;
      const n = this.viewers();
      if (n !== this._lastViewerCount) {
        this._lastViewerCount = n;
        this.io.to(ROOM).emit('viewers', { count: n });
      }
    }, this.cfg.VIEWER_BROADCAST_MS);
  }

  nowPlaying() {
    const loop = this.cfg.LIVE_LOOP && !this.paused;
    return {
      running: Boolean(this.proc),
      lifecycle: this.lifecycle,
      failures: this.failures,
      current: this.current,
      next: loop && this.playlist.length ? this.playlist[this.nextIndex % this.playlist.length] : null,
      loop,
      viewers: this.viewers(),
      mock: this.cfg.USE_MOCK,
      gui: this.gui,
      driver: this.driver.public(),
      playback: this.playback,
    };
  }

  _follow(socket, id) {
    const prev = socket.data.follow;
    if (prev) socket.leave(`veh:${prev}`);
    socket.data.follow = null;
    if (id === null || id === undefined) return;
    const parsed = VehicleIdSchema.safeParse(id);
    if (!parsed.success) return;
    socket.data.follow = parsed.data;
    socket.join(`veh:${parsed.data}`);
    const detail = this._detail(parsed.data);
    // gone: true = the car is not (or no longer) in the simulation; false = no vehicle data yet
    socket.emit('vehicle', detail ?? { id: parsed.data, gone: this.vehicleIndex.size > 0 });
  }

  /** Map rows for every viewer: [id, x, y, angle, flags]. */
  /**
   * Compact map rows [id, x, y, angle, flags, kind] (MAP_FIELDS). kind is the vehicle category, null for
   * an ordinary car (most rows) to keep the frame small; older simulations without it give null too.
   */
  _mapRows(msg) {
    const f = msg.fields ?? [];
    const ix = ['id', 'x', 'y', 'angle', 'flags'].map((k) => f.indexOf(k));
    if (ix.some((i) => i < 0)) return [];
    const k = f.indexOf('kind');
    return (msg.rows ?? []).map((r) => {
      const row = ix.map((i) => r[i]);
      row.push(k >= 0 && r[k] !== 'car' ? r[k] ?? null : null);
      return row;
    });
  }

  snapshot() {
    return {
      network: this.network,
      vehicles: this.latestVehicles ? { t: this.latestVehicles.t, fields: MAP_FIELDS, rows: this._mapRows(this.latestVehicles) } : null,
      now_playing: this.nowPlaying(),
      status: this.lastStatus,
      latest: this.latestState,
      states: this.states,
      events: this.events,
      result: this.lastResult,
      history_seconds: this.cfg.HISTORY_SECONDS,
      driver: { seat: this.driver.public(), state: this.driver.state },
    };
  }

  // ------------------------------------------------------------------ lifecycle
  /**
   * Run one start/stop/restart request at a time. A second request while one is in progress is
   * refused (error code "busy") instead of interleaving with it.
   */
  async exclusive(fn) {
    if (this._op) {
      const err = new Error('Another start or stop request is in progress. Try again in a moment.');
      err.code = 'busy';
      throw err;
    }
    this._op = true;
    try {
      return await fn();
    } finally {
      this._op = false;
    }
  }

  _setLifecycle(state) {
    if (this.lifecycle === state) return;
    this.lifecycle = state;
    this.log.debug(`Simulation lifecycle: ${state}`);
  }

  /** Start a run (stopping any current one). source: 'admin' | 'playlist'; user: who asked (admin starts). */
  async start(entry, source = 'admin', user = null) {
    clearTimeout(this._loopTimer);
    this._loopTimer = null;
    if (this.proc) await this._stopProcess();
    if (this.closing) return null;
    if (source === 'admin') {
      this.paused = false; // admin start resumes the loop afterwards (if enabled)
      this.failures = 0;
    }
    this.driver.onRunEnded(); // a drive belongs to one run
    this.playback = { paused: false, speed: 1 }; // every run starts playing at normal speed
    this._setLifecycle('STARTING');

    this._dropPendingState(); // never deliver a state of the previous run after this one starts
    const runId = `live-${Date.now().toString(36)}-${crypto.randomBytes(3).toString('hex')}`;
    this.current = { run_id: runId, entry, source, started_at: new Date().toISOString(), status: 'running',
      started_by: user ? { id: user.id, name: user.name } : null };
    this.states = [];
    this.events = [];
    this.latestState = null;
    this.lastStatus = null;
    this.lastResult = null;
    this.lastSampleT = -Infinity;
    this.sawFinalStatus = false;
    this.network = null;
    this.latestVehicles = null;
    this.vehicleIndex = new Map();
    this.followerOf = new Map();
    clearTimeout(this._vehiclesTimer);
    this._vehiclesTimer = null;
    this._pendingVehicles = null;
    this.db.insertRun({ id: runId, scenario: entry.scenario, controller: entry.controller, nav: entry.nav,
      seed: entry.seed, penetration: entry.penetration, loss: entry.loss, relay: entry.relay,
      eco_fraction: entry.eco_fraction ?? 0, ev_routing: entry.ev_routing ?? 'off', demand: entry.demand ?? 1, source,
      started_by: user?.id ?? null });
    this.stats.runs_started += 1;

    const args = buildSimArgs(entry, runId, this.cfg, { gui: this.gui });
    this.log.info(`Starting live run ${runId} (${source}): ${this.cfg.PYTHON_BIN} ${args.join(' ')}`);
    const proc = new SimProcess({ pythonBin: this.cfg.PYTHON_BIN, args, cwd: this.cfg.PROJECT_ROOT, logger: this.log });
    proc.gui = this.gui;
    this.proc = proc;
    proc.on('message', (msg) => this._onMessage(proc, msg));
    proc.on('bad_line', (line) => {
      this.stats.bad_lines += 1;
      this.log.warn('Ignoring non-protocol line from simulation:', line.slice(0, 200));
    });
    proc.on('stderr', (line) => this.log.debug('[sim]', line));
    proc.on('exit', (info) => this._onExit(proc, info));
    this.io.to(ROOM).emit('now_playing', this.nowPlaying());
    return runId;
  }

  /**
   * Turn the SUMO-GUI window on or off. SUMO cannot add a window to a running simulation, so a
   * running live run is restarted (same entry) with or without the window.
   */
  async setGui(enabled) {
    if (this.cfg.USE_MOCK) throw new Error('SUMO-GUI is not available with the mock simulation');
    this.gui = Boolean(enabled);
    let restarted = false;
    if (this.proc && this.current && Boolean(this.proc.gui) !== this.gui) {
      await this.start(this.current.entry, this.current.source, this.current.started_by);
      restarted = true;
    }
    this.io.to(ROOM).emit('now_playing', this.nowPlaying());
    return { enabled: this.gui, restarted };
  }

  /**
   * Pause, resume or change the speed of the running simulation. This changes only wall-clock
   * pacing (the simulated seconds are the same); the simulation confirms with a 'playback' message,
   * which is what viewers are shown.
   */
  playbackCommand(cmd) {
    if (!this.proc || this.lifecycle !== 'RUNNING') {
      const err = new Error('No simulation is running');
      err.code = 'not_running';
      throw err;
    }
    if (!this.proc.send(cmd)) {
      const err = new Error('The simulation is not accepting commands');
      err.code = 'not_running';
      throw err;
    }
    return true;
  }

  /** Start the current (or last) run again from its first second, with the same settings. */
  async restart(user) {
    if (!this.current) {
      const err = new Error('There is no run to restart');
      err.code = 'not_running';
      throw err;
    }
    return this.start(this.current.entry, 'admin', user);
  }

  /**
   * A platform event (run started, ended, failed; connection limit reached) in the same list as the
   * simulation's own events, so the event centre and the bell show it. Kinds start with sim_ or
   * are system warnings; `system: true` marks them.
   */
  systemEvent(kind, detail) {
    const runId = this.current?.run_id ?? null;
    const ev = { type: 'event', run_id: runId, t: Number(this.latestState?.t) || 0, kind, tls: null, detail, system: true };
    this.events.push(ev);
    if (this.events.length > this.cfg.EVENT_HISTORY) this.events.splice(0, this.events.length - this.cfg.EVENT_HISTORY);
    if (runId) this._persist('an event', () => this.db.saveEvent(runId, ev));
    this._emitReliable('event', ev);
    this.stats.events_broadcast += 1;
  }

  /** Admin stop: stop the run and pause the playlist until the next admin start. */
  async stop() {
    this.paused = true;
    clearTimeout(this._loopTimer);
    this._loopTimer = null;
    const had = Boolean(this.proc);
    if (had) await this._stopProcess();
    this.io.to(ROOM).emit('now_playing', this.nowPlaying());
    return had;
  }

  async _stopProcess() {
    const proc = this.proc;
    if (!proc) return;
    proc.manualStop = true;
    this._setLifecycle('STOPPING');
    await proc.stop();
  }

  /** Start the playlist (LIVE_LOOP). */
  startLoop() {
    if (!this.cfg.LIVE_LOOP || !this.playlist.length) return;
    this.paused = false;
    if (!this.proc) this._startNextFromPlaylist();
  }

  _startNextFromPlaylist() {
    if (this.closing || this.paused || !this.playlist.length) return;
    const entry = this.playlist[this.nextIndex % this.playlist.length];
    this.nextIndex = (this.nextIndex + 1) % this.playlist.length;
    this.start(entry, 'playlist').catch((err) => this.log.error('Playlist start failed:', err.message));
  }

  /**
   * Store something about the run. A database failure must not stop the live stream or crash the
   * gateway: it is counted, logged (at most once every 10 s) and the broadcast goes on.
   */
  _persist(what, fn) {
    try {
      return fn();
    } catch (err) {
      this.stats.persist_errors += 1;
      if (Date.now() - this._lastPersistLog > 10_000) {
        this._lastPersistLog = Date.now();
        this.log.error(`Could not store ${what} (${this.stats.persist_errors} storage errors so far):`, err.message);
      }
      return null;
    }
  }

  // ------------------------------------------------------------------ messages
  _onMessage(proc, msg) {
    if (proc !== this.proc || !this.current) return; // late line from a replaced process
    this.stats.lines_in += 1;
    const runId = this.current.run_id;
    switch (msg.type) {
      case 'state':
        if (this.lifecycle === 'STARTING') {
          this._setLifecycle('RUNNING');
          this.io.to(ROOM).emit('now_playing', this.nowPlaying());
        }
        this.stats.states_in += 1;
        this._recordState(runId, msg);
        this._broadcastState(msg);
        break;
      case 'event': {
        const ev = { type: 'event', run_id: runId, t: msg.t, kind: msg.kind, tls: msg.tls, detail: msg.detail };
        this.events.push(ev);
        if (this.events.length > this.cfg.EVENT_HISTORY) this.events.splice(0, this.events.length - this.cfg.EVENT_HISTORY);
        this._persist('an event', () => this.db.saveEvent(runId, ev));
        this._emitReliable('event', ev);
        this.stats.events_broadcast += 1;
        break;
      }
      case 'status':
        this.lastStatus = msg;
        if (msg.status === 'started' && this.lifecycle === 'STARTING') {
          this._setLifecycle('RUNNING');
          this.io.to(ROOM).emit('now_playing', this.nowPlaying());
        }
        if (msg.status === 'started') {
          const e = this.current?.entry;
          this.systemEvent('sim_started', e ? `${e.label ? `${e.label}: ` : ''}${e.scenario} with ${e.controller}` : msg.message);
        }
        if (['finished', 'stopped', 'error'].includes(msg.status)) {
          this.sawFinalStatus = true;
          this.driver._broadcast(); // no new drives in a run that is ending
        }
        this._emitReliable('status', msg);
        break;
      case 'network':
        this.network = { ...msg, run_id: runId };
        this._emitReliable('network', this.network);
        break;
      case 'vehicles':
        this._onVehicles(msg);
        break;
      case 'driver':
        this.driver.onSimMessage({ ...msg, run_id: runId });
        break;
      case 'playback':
        this.playback = { paused: Boolean(msg.paused), speed: Number(msg.speed) || 1 };
        this.io.to(ROOM).emit('now_playing', this.nowPlaying());
        break;
      case 'routes': {
        const q = this._routeQueries.get(String(msg.qid));
        if (q) {
          this._routeQueries.delete(String(msg.qid));
          clearTimeout(q.timer);
          const { type: _t, qid: _q, ...answer } = msg;
          q.cb(answer);
        }
        break;
      }
      case 'result':
        this.lastResult = msg;
        this._persist('the result', () => this.db.saveResult(runId, msg.metrics));
        this._emitReliable('result', msg);
        break;
      default:
        break;
    }
  }

  _recordState(runId, msg) {
    this.latestState = msg;
    this.states.push(historyRow(msg, runId));
    const t = Number(msg.t) || 0;
    const minT = t - this.cfg.HISTORY_SECONDS;
    let drop = 0;
    while (drop < this.states.length && (Number(this.states[drop].t) || 0) <= minT) drop += 1;
    if (drop) this.states.splice(0, drop);
    if (t - this.lastSampleT >= this.cfg.SAMPLE_EVERY_SIM_S) {
      this.lastSampleT = t;
      this._persist('a state sample', () => this.db.saveSample(runId, msg));
    }
  }

  /** At most one "state" per STATE_INTERVAL_MS, always the latest; volatile (droppable for slow clients). */
  _broadcastState(msg) {
    const wait = this._lastStateEmit + this.cfg.STATE_INTERVAL_MS - Date.now();
    if (wait <= 0) {
      this._dropPendingState(); // a late timer must not send an older state right after this one
      this._emitState(msg);
    } else {
      this._pendingState = msg;
      if (!this._stateTimer) {
        this._stateTimer = setTimeout(() => {
          this._stateTimer = null;
          const pending = this._pendingState;
          this._pendingState = null;
          if (pending) this._emitState(pending);
        }, wait);
      }
    }
  }

  _emitState(msg) {
    this._lastStateEmit = Date.now();
    const payload = { ...msg, sent_at: this._lastStateEmit }; // sent_at: lets clients measure latency
    this.io.to(ROOM).volatile.emit('state', payload);
    this.stats.states_broadcast += 1;
    this.stats.bytes_broadcast += Buffer.byteLength(JSON.stringify(payload)) * this.viewers();
  }

  /**
   * A vehicle's detail with its neighbours in the lane: `ahead` (the vehicle it follows) and `behind`
   * (the vehicle following it), each { id, speed, acc, gap, kind, flags, follow, follow_speed, ttc }.
   * The gap of `ahead` is this vehicle's gap; of `behind`, the follower's gap to this vehicle.
   */
  _detail(id) {
    const d = this.vehicleIndex.get(id);
    if (!d) return undefined;
    const side = (o, gap) => (o ? {
      id: o.id, speed: o.speed, acc: o.acc ?? null, gap: gap ?? null, kind: o.kind ?? null, flags: o.flags,
      follow: o.follow ?? null, follow_speed: o.follow_speed ?? null, ttc: o.ttc ?? null,
    } : null);
    const back = this.followerOf.get(id);
    return { ...d, ahead: side(d.leader ? this.vehicleIndex.get(String(d.leader)) : null, d.gap), behind: side(back, back?.gap) };
  }

  _onVehicles(msg) {
    this.latestVehicles = msg;
    const fields = msg.fields ?? [];
    const index = new Map();
    const followerOf = new Map(); // leader id -> the vehicle directly behind it (smallest gap)
    for (const row of msg.rows ?? []) {
      const obj = { t: msg.t };
      fields.forEach((k, i) => { obj[k] = row[i]; });
      index.set(String(obj.id), obj);
      if (obj.leader != null) {
        const prev = followerOf.get(String(obj.leader));
        if (!prev || (obj.gap ?? Infinity) < (prev.gap ?? Infinity)) followerOf.set(String(obj.leader), obj);
      }
    }
    this.vehicleIndex = index;
    this.followerOf = followerOf;
    // Same throttle as states: at most one map frame per STATE_INTERVAL_MS, always the latest.
    // Never in the same tick as the state broadcast: a volatile message is dropped while the
    // socket is still writing the previous one, so the frame goes out VEHICLES_OFFSET_MS later.
    this._pendingVehicles = msg;
    if (this._vehiclesTimer) return;
    const wait = Math.max(VEHICLES_OFFSET_MS, this._lastVehiclesEmit + this.cfg.STATE_INTERVAL_MS - Date.now());
    this._vehiclesTimer = setTimeout(() => {
      this._vehiclesTimer = null;
      const p = this._pendingVehicles;
      this._pendingVehicles = null;
      if (p) this._emitVehicles(p);
    }, wait);
  }

  _emitVehicles(msg) {
    this._lastVehiclesEmit = Date.now();
    const payload = { run_id: this.current?.run_id, t: msg.t, fields: MAP_FIELDS, rows: this._mapRows(msg), sent_at: this._lastVehiclesEmit };
    this.io.to(ROOM).volatile.emit('vehicles', payload);
    this.stats.bytes_broadcast += Buffer.byteLength(JSON.stringify(payload)) * this.viewers();
    // Detail rows only to the viewers following each car. Reliable (not volatile): they are small,
    // go only to followers, and would otherwise be dropped right after the volatile map frame.
    for (const [room, members] of this.io.of('/').adapter.rooms) {
      if (!room.startsWith('veh:') || !members.size) continue;
      const id = room.slice(4);
      const detail = this._detail(id) ?? { id, t: msg.t, gone: true };
      this.io.to(room).emit('vehicle', detail);
    }
  }

  _emitReliable(name, payload) {
    this.io.to(ROOM).emit(name, payload);
  }

  _dropPendingState() {
    clearTimeout(this._stateTimer);
    this._stateTimer = null;
    this._pendingState = null;
  }

  _onExit(proc, { code, signal, stopped }) {
    if (proc !== this.proc) return;
    // A pending final state is still sent by its timer, keeping the at-most-once-per-interval rule.
    this.proc = null;
    this.driver.onRunEnded();
    const run = this.current;
    let status;
    if (stopped) status = 'stopped';
    else if (code === 0 && this.lastResult) status = 'finished';
    else status = 'error';
    const message = stopped ? 'Stopped by admin' : status === 'error'
      ? `Simulation exited with code ${code}${signal ? ` (${signal})` : ''}${this.lastStatus?.message ? `: ${this.lastStatus.message}` : ''}`
      : 'Simulation completed';
    if (run) {
      run.status = status;
      this._persist('the end of the run', () => this.db.endRun(run.run_id, status, message));
      // Windows kills cannot be caught by Python, so report the final status ourselves if needed
      if (!this.sawFinalStatus) {
        this.lastStatus = { type: 'status', run_id: run.run_id, status, message };
        this._emitReliable('status', this.lastStatus);
      }
    }
    if (proc.gui && !stopped && this.lastStatus?.status === 'stopped') {
      // Python reports "stopped" when the SUMO window was closed: the viewer is done with the GUI
      this.gui = false;
      this.log.info('SUMO-GUI window was closed; continuing without the window');
    }
    this.log.info(`Live run ${run?.run_id} ended: ${status} (code ${code})`);
    if (run) this.systemEvent(status === 'finished' ? 'sim_finished' : status === 'stopped' ? 'sim_stopped' : 'sim_failed', message);

    // Bounded recovery: after MAX_SIM_FAILURES failed runs in a row the playlist stops retrying
    this.failures = status === 'error' ? this.failures + 1 : 0;
    let loops = !this.closing && !this.paused && this.cfg.LIVE_LOOP && !proc.manualStop;
    if (loops && this.failures >= this.cfg.MAX_SIM_FAILURES) {
      loops = false;
      this.paused = true;
      this.log.error(`${this.failures} runs failed in a row: the playlist is paused until an administrator starts a run`);
      this.systemEvent('sim_halted', `${this.failures} runs failed in a row: the playlist is paused until a run is started from Run control`);
    }
    if (status === 'error') this._setLifecycle(loops ? 'RECOVERING' : 'FAILED');
    else this._setLifecycle('STOPPED');
    this.io.to(ROOM).emit('now_playing', this.nowPlaying());

    if (loops) {
      const delay = status === 'error' ? this.cfg.CRASH_RETRY_MS : 1500;
      this._loopTimer = setTimeout(() => this._startNextFromPlaylist(), delay);
    }
  }

  async close() {
    this.closing = true;
    clearTimeout(this._loopTimer);
    clearTimeout(this._stateTimer);
    clearTimeout(this._viewerTimer);
    clearTimeout(this._vehiclesTimer);
    this.driver.close();
    if (this.proc) await this._stopProcess();
  }
}
